import asyncio
import csv
import io
import json
import os
from contextlib import asynccontextmanager, suppress
from typing import Literal
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func, delete, update
from openpyxl import Workbook
from .config import IMAGES, MODE
from .db import Session, init_db, Product, Variant, ProductImage, Job, JobItem, Log, now, WorkspaceState
from .worker import recover, worker_loop, log

@asynccontextmanager
async def lifespan(app):
    init_db()
    recover()
    task = asyncio.create_task(worker_loop())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

app = FastAPI(title='AliExpress Local Collector', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:3000', 'http://127.0.0.1:3000'], allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])

@app.middleware('http')
async def local_writes(request: Request, call_next):
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin not in ('http://localhost:3000', 'http://127.0.0.1:3000', 'http://localhost:8000', 'http://127.0.0.1:8000'):
        return JSONResponse({'detail': '로컬 관리자 화면에서만 변경할 수 있습니다.'}, status_code=403)
    return await call_next(request)

app.mount('/images', StaticFiles(directory=IMAGES), name='images')

class JobRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    start_page: int = Field(default=1, ge=1, le=1000)
    end_page: int = Field(default=10, ge=1, le=1000)
    page_size: int = Field(default=50, ge=1, le=50)
    country: Literal['KR'] = 'KR'
    currency: Literal['KRW', 'USD'] = 'KRW'
    sort: Literal['', 'SALE_PRICE_ASC', 'SALE_PRICE_DESC', 'LAST_VOLUME_DESC'] = ''
    source: Literal['mock', 'live'] = 'mock'
    @model_validator(mode='after')
    def validate_pages(self):
        self.keyword = self.keyword.strip()
        if not self.keyword or self.end_page < self.start_page or self.end_page - self.start_page > 99:
            raise ValueError('키워드를 입력하고 한 작업에 최대 100페이지의 올바른 범위를 지정하세요.')
        return self

class Selection(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)

class StateRequest(BaseModel):
    payload: dict

@app.get('/api/workspace/{key}')
def read_workspace(key: Literal['sales', 'words', 'product-meta']):
    with Session() as db:
        item = db.get(WorkspaceState, key)
        return item.payload if item else {}

@app.post('/api/workspace/{key}')
def save_workspace(key: Literal['sales', 'words', 'product-meta'], body: StateRequest):
    if len(json.dumps(body.payload)) > 2_000_000:
        raise HTTPException(413, '저장할 설정이 너무 큽니다.')
    with Session.begin() as db:
        db.merge(WorkspaceState(key=key, payload=body.payload))
    return {'ok': True}

def required(db, model, id):
    obj = db.get(model, id)
    if not obj:
        raise HTTPException(404, '항목을 찾을 수 없습니다.')
    return obj

def job_dict(db, job):
    counts = dict(db.execute(select(JobItem.status, func.count()).where(JobItem.job_id == job.id).group_by(JobItem.status)).all())
    pages_done = max(0, job.next_page - job.config['start_page'])
    pages_total = max(0, job.config['end_page'] - job.config['start_page'] + 1)
    processed = counts.get('done', 0) + counts.get('skipped', 0) + counts.get('failed', 0)
    total = sum(counts.values())
    progress = round((pages_done + processed / max(total, 1)) / (pages_total + 1) * 100) if pages_total else round(processed / max(total, 1) * 100)
    if job.status in ('completed', 'completed_with_errors'):
        progress = 100
    return dict(id=job.id, source=job.source, keyword=job.keyword, config=job.config, status=job.status,
                next_page=job.next_page, pages_done=pages_done, pages_total=pages_total, results=job.results,
                duplicates=job.duplicates, counts=counts, progress=min(100, progress), error=job.error, created_at=job.created_at)

@app.get('/api/health')
def health():
    return {'ok': True}

@app.get('/api/settings')
def settings():
    return {'default_mode': MODE, 'credentials_present': bool(os.getenv('ALI_APP_KEY') and os.getenv('ALI_APP_SECRET')),
            'ds_enabled': os.getenv('ALI_ENABLE_DS', 'false').lower() == 'true',
            'notes': ['공식 API 검색 결과는 웹사이트 검색 결과와 다를 수 있습니다.', '실제 계정의 API 권한 및 전체 SKU 제공 여부는 연결 후 확인이 필요합니다.', '배송 견적 미지원 · 제공되지 않은 값은 null로 저장합니다.']}

@app.post('/api/jobs')
def create_job(body: JobRequest):
    if body.source == 'live' and not (os.getenv('ALI_APP_KEY') and os.getenv('ALI_APP_SECRET')):
        raise HTTPException(400, '공식 API 인증정보가 없습니다. .env를 설정하거나 샘플 모드를 선택하세요.')
    with Session.begin() as db:
        job = Job(source=body.source, keyword=body.keyword, config=body.model_dump(), next_page=body.start_page)
        db.add(job)
        db.flush()
        log(db, job.id, f'{body.source} 수집 요청 등록')
        return {'id': job.id}

@app.get('/api/jobs')
def jobs():
    with Session() as db:
        return [job_dict(db, j) for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(50))]

@app.post('/api/jobs/{job_id}/{action}')
def control_job(job_id: int, action: Literal['pause', 'resume', 'cancel', 'retry']):
    with Session.begin() as db:
        job = required(db, Job, job_id)
        if action == 'pause' and job.status in ('queued', 'running'):
            job.status = 'paused'
        elif action == 'resume' and job.status in ('paused', 'failed'):
            job.status, job.error = 'queued', None
        elif action == 'cancel' and job.status in ('queued', 'running', 'paused', 'failed'):
            job.status = 'cancelled'
            db.execute(update(JobItem).where(JobItem.job_id == job_id, JobItem.status.in_(['pending', 'running'])).values(status='cancelled'))
        elif action == 'retry' and job.status in ('completed_with_errors', 'failed', 'paused', 'completed'):
            db.execute(update(JobItem).where(JobItem.job_id == job_id, JobItem.status == 'failed').values(status='pending', error=None))
            job.status, job.error = 'queued', None
        else:
            raise HTTPException(409, '현재 상태에서 실행할 수 없는 작업입니다.')
        job.updated_at = now()
        log(db, job_id, f'사용자 요청: {action}')
    return {'ok': True}

@app.get('/api/jobs/{job_id}/logs')
def logs(job_id: int, after: int = 0):
    with Session() as db:
        rows = db.scalars(select(Log).where(Log.job_id == job_id, Log.id > after).order_by(Log.id.desc()).limit(200))
        return [dict(id=r.id, level=r.level, message=r.message, created_at=r.created_at) for r in rows]

def product_summary(db, p):
    image = db.scalar(select(ProductImage).where(ProductImage.product_id == p.id, ProductImage.path.is_not(None)).order_by(ProductImage.position).limit(1))
    count = db.scalar(select(func.count()).select_from(Variant).where(Variant.product_id == p.id))
    return dict(id=p.id, source=p.source, external_id=p.external_id, title=p.title, status=p.status,
                option_status=p.option_status, price=p.payload.get('price'), currency=p.payload.get('currency'),
                url=p.payload.get('url'), image_path=image.path if image else None, variant_count=count,
                collected_at=p.collected_at, updated_at=p.updated_at)

def filtered(query, q, source):
    if q:
        query = query.where(Product.title.contains(q, autoescape=True))
    if source:
        query = query.where(Product.source == source)
    return query

@app.get('/api/products')
def products(q: str = '', source: Literal['mock', 'live', ''] = '', page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    with Session() as db:
        total = db.scalar(filtered(select(func.count()).select_from(Product), q, source))
        rows = db.scalars(filtered(select(Product), q, source).order_by(Product.id.desc()).offset((page-1)*page_size).limit(page_size))
        return {'total': total, 'page': page, 'items': [product_summary(db, p) for p in rows]}

@app.get('/api/duplicates')
def duplicates():
    with Session() as db:
        groups = db.execute(select(Product.source, Product.external_id, func.count()).group_by(Product.source, Product.external_id).having(func.count() > 1)).all()
        return {'duplicate_groups': [list(g) for g in groups], 'prevented': db.scalar(select(func.coalesce(func.sum(Job.duplicates), 0))), 'policy': '모드 + 상품 ID 고유 제약으로 중복 저장 방지'}

@app.post('/api/products/delete')
def delete_products(body: Selection):
    with Session.begin() as db:
        active = db.scalar(select(Job.id).where(Job.status.in_(['queued', 'running'])).limit(1))
        in_flight = db.scalar(select(JobItem.id).where(JobItem.status == 'running').limit(1))
        if active or in_flight:
            raise HTTPException(409, '진행 중인 수집을 일시정지하거나 완료한 후 삭제하세요.')
        # Files are retained deliberately; another product may reference the same image in future.
        db.execute(delete(Variant).where(Variant.product_id.in_(body.ids)))
        db.execute(delete(ProductImage).where(ProductImage.product_id.in_(body.ids)))
        result = db.execute(delete(Product).where(Product.id.in_(body.ids)))
        return {'deleted': result.rowcount}

@app.get('/api/products/{product_id}')
def detail(product_id: int):
    with Session() as db:
        p = required(db, Product, product_id)
        return {**product_summary(db, p), 'data': p.payload,
                'variants': [v.payload for v in db.scalars(select(Variant).where(Variant.product_id == p.id).order_by(Variant.position))],
                'images': [dict(role=i.role, url=i.url, path=i.path, error=i.error) for i in db.scalars(select(ProductImage).where(ProductImage.product_id == p.id).order_by(ProductImage.position))]}

@app.post('/api/products/{product_id}/recollect')
def recollect(product_id: int):
    with Session.begin() as db:
        p = required(db, Product, product_id)
        context = p.payload.get('collection_context', {})
        config = dict(keyword=context.get('keyword', p.title or p.external_id), start_page=1, end_page=0, page_size=1,
                      country=context.get('country', 'KR'), currency=context.get('currency', 'KRW'), sort='', force=True)
        job = Job(source=p.source, keyword=config['keyword'], config=config, next_page=1)
        db.add(job)
        db.flush()
        seed = p.payload.get('raw', {}).get('mock_seed') or {'product_id': p.external_id}
        db.add(JobItem(job_id=job.id, external_id=p.external_id, seed=seed))
        return {'id': job.id}

def safe_cell(value):
    if value is None:
        return ''
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value

@app.get('/api/export/{kind}')
def export(kind: Literal['csv', 'xlsx'], q: str = '', source: Literal['mock', 'live', ''] = '', ids: str = ''):
    selected = None
    if ids:
        try:
            selected = [int(i) for i in ids.split(',')]
            if len(selected) > 1000:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, '선택 상품 ID 형식 오류')
    header = ['mode', 'product_id', 'title', 'url', 'price', 'currency', 'option_status', 'sku_id', 'original_options_json', 'combination', 'sku_price', 'sku_currency', 'stock', 'available', 'image_path', 'collected_at', 'product_json', 'sku_json']
    def rows():
        yield header
        with Session() as db:
            query = filtered(select(Product), q, source).order_by(Product.id)
            if selected:
                query = query.where(Product.id.in_(selected))
            for p in db.scalars(query.execution_options(yield_per=100)):
                variants = list(db.scalars(select(Variant).where(Variant.product_id == p.id).order_by(Variant.position)))
                for v in variants or [None]:
                    data = v.payload if v else {}
                    yield [safe_cell(x) for x in [p.source, p.external_id, p.title, p.payload.get('url'), p.payload.get('price'), p.payload.get('currency'), p.option_status,
                           data.get('sku_id'), json.dumps(data.get('options'), ensure_ascii=False), data.get('combination'), data.get('price'), data.get('currency'), data.get('stock'), data.get('available'), data.get('image_path'), p.collected_at,
                           json.dumps(p.payload, ensure_ascii=False), json.dumps(data, ensure_ascii=False)]]
    if kind == 'csv':
        def stream():
            yield '\ufeff'
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for row in rows():
                writer.writerow(row)
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
        return StreamingResponse(stream(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename=products.csv'})
    book = Workbook(write_only=True)
    sheet = book.create_sheet('Products and original SKUs')
    for row in rows():
        sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    output.seek(0)
    return StreamingResponse(output, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename=products.xlsx'})

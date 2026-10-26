import asyncio
from sqlalchemy import select, delete, func, update
from .db import Session, Product, Variant, ProductImage, Job, JobItem, Log, now
from .providers import OfficialProvider, MockProvider
from .images import save_image

def log(db, job_id, message, level='info'):
    db.add(Log(job_id=job_id, message=message, level=level))

def recover():
    with Session.begin() as db:
        db.execute(update(JobItem).where(JobItem.status == 'running').values(status='pending'))
        for job in db.scalars(select(Job).where(Job.status.in_(['running', 'queued']))):
            job.status = 'paused'
            log(db, job.id, '프로그램 재시작: 체크포인트 복구 완료. 재개를 누르면 남은 작업만 실행합니다.')

async def process_one(job_id, provider):
    with Session.begin() as db:
        job = db.get(Job, job_id)
        if not job or job.status not in ('queued', 'running'):
            return
        job.status = 'running'
        job.updated_at = now()
        config, source = dict(job.config), job.source
        item = db.scalar(select(JobItem).where(JobItem.job_id == job_id, JobItem.status == 'pending').order_by(JobItem.id).limit(1))
        if item:
            existing = db.scalar(select(Product).where(Product.source == source, Product.external_id == item.external_id))
            if existing and not config.get('force'):
                item.status = 'skipped'
                job.duplicates += 1
                return
            item.status = 'running'
            item.attempts += 1
            item_id, seed = item.id, dict(item.seed)
        else:
            item_id, seed = None, None
        page = job.next_page
        if item_id is None and page > config['end_page']:
            failed = db.scalar(select(func.count()).select_from(JobItem).where(JobItem.job_id == job_id, JobItem.status == 'failed'))
            job.status = 'completed_with_errors' if failed else 'completed'
            log(db, job_id, '수집 작업 완료' + (' (실패 상품 있음)' if failed else ''))
            return
    if item_id is None:
        # Page items and checkpoint commit atomically; an interrupted page is safe to repeat.
        try:
            rows = await provider.search(config, page)
            with Session.begin() as db:
                job = db.get(Job, job_id)
                if job.status == 'cancelled':
                    return
                seen = set(db.scalars(select(JobItem.external_id).where(JobItem.job_id == job_id)))
                for row in rows:
                    if row.get('product_id') is None:
                        log(db, job_id, '상품 ID가 없는 검색 결과 제외', 'warning')
                        continue
                    pid = str(row['product_id'])
                    if pid in seen:
                        job.duplicates += 1
                        continue
                    seen.add(pid)
                    db.add(JobItem(job_id=job_id, external_id=pid, seed=row))
                job.results += len(rows)
                job.next_page = page + 1
                log(db, job_id, f'{page}페이지 검색 완료: {len(rows)}개')
        except Exception as exc:
            with Session.begin() as db:
                job = db.get(Job, job_id)
                if job.status != 'cancelled':
                    job.status = 'failed'
                    job.error = safe_error(exc)
                    log(db, job_id, f'{page}페이지 실패: {job.error}', 'error')
        return
    try:
        product = await provider.detail(seed, config)
        product['collection_context'] = {'currency': config['currency'], 'country': config['country'], 'keyword': config['keyword']}
        images, unique = [], set()
        for entry in product.pop('images'):
            key = (entry['role'], entry.get('url'))
            if key in unique:
                continue
            unique.add(key)
            with Session() as db:
                if db.get(Job, job_id).status == 'cancelled':
                    return
            try:
                path = await save_image(source, product['external_id'], entry)
                images.append({**entry, 'path': path, 'error': None})
            except Exception as exc:
                images.append({**entry, 'path': None, 'error': safe_error(exc)})
        variants = product.pop('variants')
        with Session.begin() as db:
            job = db.get(Job, job_id)
            item = db.get(JobItem, item_id)
            if job.status == 'cancelled':
                return
            p = db.scalar(select(Product).where(Product.source == source, Product.external_id == product['external_id']))
            if p is None:
                p = Product(source=source, external_id=product['external_id'], title=product['title'], status='collected', option_status=product['option_status'], payload=product)
                db.add(p)
                db.flush()
            p.title, p.payload, p.updated_at = product['title'], product, now()
            p.option_status = product['option_status']
            p.status = 'partial' if p.option_status != 'complete' or any(i['error'] for i in images) else 'collected'
            db.execute(delete(Variant).where(Variant.product_id == p.id))
            db.execute(delete(ProductImage).where(ProductImage.product_id == p.id))
            for position, v in enumerate(variants):
                v['image_path'] = next((i['path'] for i in images if i['url'] and i['url'] == v.get('image_url')), None)
                db.add(Variant(product_id=p.id, position=position, sku_id=v.get('sku_id'), payload=v))
            for position, image in enumerate(images):
                db.add(ProductImage(product_id=p.id, position=position, **image))
                if image['error']:
                    log(db, job_id, f'{p.external_id} 이미지 미수집: {image["error"]}', 'warning')
            if source == 'live' and p.option_status != 'complete':
                log(db, job_id, f'{p.external_id}: ' + ' / '.join(product['notes']), 'warning')
            item.status, item.error = 'done', None
    except Exception as exc:
        with Session.begin() as db:
            item = db.get(JobItem, item_id)
            if item.status != 'cancelled':
                item.status, item.error = 'failed', safe_error(exc)
                log(db, job_id, f'{item.external_id}: {item.error}', 'error')

def safe_error(exc):
    # Avoid logging HTTP request URLs, signed payloads or credential material.
    from .providers import ProviderError
    if isinstance(exc, (ProviderError, ValueError)):
        return str(exc)[:800]
    return f'{type(exc).__name__}: 처리 실패 (네트워크·응답 형식·파일 접근 확인 필요)'

async def worker_loop():
    mock = MockProvider()
    official = None
    while True:
        try:
            with Session() as db:
                job = db.scalar(select(Job).where(Job.status.in_(['queued', 'running'])).order_by(Job.id).limit(1))
                job_id, source = (job.id, job.source) if job else (None, None)
            if job_id is None:
                await asyncio.sleep(0.3)
                continue
            if source == 'live' and official is None:
                official = OfficialProvider()
            await process_one(job_id, mock if source == 'mock' else official)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if job_id:
                with Session.begin() as db:
                    job = db.get(Job, job_id)
                    job.status, job.error = 'failed', safe_error(exc)
                    log(db, job_id, job.error, 'error')
            await asyncio.sleep(1)

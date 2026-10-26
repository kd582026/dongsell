# 소싱 데스크 — AliExpress 로컬 상품 수집기

Windows용 첫 번째 실행 버전입니다. Next.js + TypeScript + Tailwind CSS, FastAPI, SQLAlchemy, SQLite를 사용합니다. Docker와 국내 마켓 업로드 기능은 없습니다.

## 실행

1. Python 3.11 이상과 Node.js 22 LTS 이상을 설치합니다. Python 설치 시 **Add Python to PATH**를 선택하세요.
2. 프로젝트의 **start.bat**을 더블클릭합니다. 첫 실행은 인터넷으로 의존성을 설치하고 화면을 빌드합니다.
3. **http://localhost:3000**이 열립니다. 첫 실행은 API 키가 필요 없는 **샘플 모드**입니다.
4. 기본 검색어 `나사`에서 **1페이지 테스트**를 누릅니다. 상품 50개, 각 상품에 원본 샘플 옵션 3개가 저장됩니다.
5. 결과를 확인하고 시작 1 / 종료 10 / 페이지당 50으로 **수집 시작**을 누릅니다. 기존 50개는 건너뛰고 나머지를 수집합니다.

실행 창을 유지하세요. 종료는 실행 창에서 Ctrl+C입니다. PC를 종료한 뒤 다시 실행하면 진행 중이던 작업은 **일시정지** 상태로 복구됩니다. 수집 작업 → **재개**를 눌러 남은 작업을 이어갑니다. 다른 프로그램이 3000 또는 8000 포트를 사용하면 먼저 종료해야 합니다. Codex의 번들 Python/Node만 있는 PC에서는 실행 스크립트가 이를 보조적으로 탐색합니다.

샘플 데이터는 실제 AliExpress 상품이 아닙니다. 이미지도 로컬에서 생성한 테스트 그림입니다. `MOCK-` 상품 ID 및 화면의 샘플 배지로 표시하고, 실제 데이터와 별도 고유 키를 사용합니다.

## 지원 기능

- 키워드 / 페이지 범위 / 페이지 크기 / KR 배송국가 / KRW·USD / 가격·판매량 정렬
- 서버 페이지네이션, 상품명 검색, 모드 필터, 선택 삭제, 중복 확인
- 상품 상세, 원본 옵션의 응답 순서, 이미지 확대, 개별 재수집
- CSV / Excel: 선택 상품 또는 현재 검색 전체, **SKU당 한 행**. 옵션이 없는 상품도 한 행으로 내보냅니다. CSV는 UTF-8 BOM을 포함합니다. 수식 삽입을 막기 위해 위험한 문자열 앞에는 작은따옴표를 붙입니다.
- 진행 상태 1.5초 폴링, 일시정지·재개·취소·실패 재시도, 영속 오류 로그
- API 429 `Retry-After`(초/HTTP 날짜), 지수 백오프·jitter, 요청 간격, 일시적 네트워크/5xx 재시도

## 실제 API 설정과 한계

`.env.example`을 `.env`로 복사하고 앱에 발급된 값을 입력합니다. 실행 스크립트도 파일이 없으면 복사합니다. 변경 후 서버를 재시작하세요.

```dotenv
COLLECTOR_MODE=mock
ALI_APP_KEY=
ALI_APP_SECRET=
ALI_SESSION=
ALI_TRACKING_ID=
ALI_GATEWAY=https://eco.taobao.com/router/rest
ALI_PROTOCOL=top
ALI_ENABLE_DS=false
API_INTERVAL_SECONDS=1.5
API_MAX_RETRIES=4
```

화면에서 **공식 API** 모드를 선택하면 실제 호출합니다. 인증정보가 없으면 오류로 종료하며 샘플로 몰래 대체하지 않습니다. 기본 간격은 예시 값으로, 앱에 배정된 호출 한도에 맞게 높여야 합니다.

| 범위 | 연결 API / 상태 |
|---|---|
| 검색 | `aliexpress.affiliate.product.query` |
| 기본 상품 상세 | `aliexpress.affiliate.productdetail.get` |
| SKU / 설명 / 속성 | `aliexpress.ds.product.get` — 승인된 Dropshipping 권한과 세션이 있을 때 `ALI_ENABLE_DS=true` |
| 배송비 / 배송 가능 여부 | 별도 배송 견적 API 미연결, null / 미수집 |
| 전체 옵션 보장 | 실제 API 응답만으로 보장 불가. SKU가 반환되어도 **부분 수집** |
| SKU API 권한 없음 | **미지원 / 미수집**, SKU 0개. 기본 상세는 저장 |

공식 API 검색은 Affiliate 대상 상품을 반환하므로 웹사이트 검색과 같은 상품·순서·페이지라고 가정하지 않습니다. 제목과 옵션은 API가 반환한 문자열을 변경하지 않습니다. 언어 번역 파라미터를 임의로 지정하지 않으며, API 자체의 언어 선택이 판매자가 등록한 원어와 같은지는 검증할 수 없습니다. 전체 옵션 조합을 생성하지 않고 **반환된 SKU 행만 저장**합니다. 누락 SKU ID, 판매 가능 상태, 재고 등은 null이며 재고를 기반으로 판매 가능 상태를 추정하지 않습니다.

가격은 해당 가격의 통화와 쌍으로 저장합니다. 요청 통화 대신 API 원본 통화가 돌아올 수 있습니다. 가격 환산·번역·브랜드 추정은 하지 않습니다. 표준 속성에 포함된 브랜드 원문은 원본 속성에서 확인할 수 있지만 전용 브랜드 필드는 명확한 매핑이 없어 null입니다.

기본 게이트웨이는 Alibaba TOP 문서의 HTTPS 주소 및 HMAC-MD5 서명입니다. 앱 콘솔에서 새 게이트웨이로 지정된 경우, `ALI_GATEWAY=https://api-sg.aliexpress.com/sync`, `ALI_PROTOCOL=sync`로 설정하면 epoch timestamp와 HMAC-SHA256 서명을 사용합니다. **실제 계정 키가 없는 상태에서 라이브 인증·권한·SKU 응답은 검증하지 않았습니다.** 앱별 게이트웨이/응답 구조 변화가 있으면 `providers.py`를 조정해야 하며 오류로 숨기지 않고 표시합니다. 토큰 자동 발급·갱신은 구현하지 않았습니다.

참고한 공식 문서:

- [상품 검색](https://developer.alibaba.com/docs/api.htm?apiId=45803)
- [Affiliate 상세](https://developer.alibaba.com/docs/api.htm?apiId=48595)
- [Dropshipping 상세 / SKU](https://developer.alibaba.com/docs/api.htm?apiId=60452)
- [TOP 공통 파라미터 및 호출](https://open.alitrip.com/docs/doc.htm?articleId=118934&docType=1&treeId=674)

## 저장과 작업 복구

```
data/
  collector.db          # 상품, SKU, 이미지 경로, 작업·체크포인트·로그
  images/               # 검증한 로컬 JPEG 및 샘플 이미지
  logs/                 # 실행 스크립트의 서버 로그
backend/app/
  config.py             # 환경설정
  db.py                 # 6개 SQLAlchemy 모델
  providers.py          # 공식 / 샘플 어댑터 및 응답 정규화
  images.py             # 이미지 저장
  worker.py             # 작업 체크포인트 및 단일 비동기 worker
  main.py               # API·내보내기
frontend/app/           # 한국어 관리 화면
scripts/start.ps1       # 설치·빌드·서버 시작·종료
```

테이블: `products`, `product_variants`, `product_images`, `collection_jobs`, `collection_job_items`, `collection_logs`. 상품 식별자는 `(source, external_id)` 고유 제약으로 보호합니다. 동일 상품의 재수집은 옵션/이미지 메타데이터를 한 트랜잭션으로 교체합니다. `collected_at`은 최초 날짜, `updated_at`은 마지막 성공 날짜입니다. 원본 응답은 JSON으로 보존합니다.

검색 페이지의 상품 목록과 다음 페이지 체크포인트를 원자적으로 저장합니다. 완료 상품은 작업 내 `done`, 기존 상품은 `skipped`로 기록합니다. 중단 시 `running` 항목은 다음 실행에서 `pending`으로 복구합니다. 완료 상품은 다시 요청하지 않습니다. 재수집 버튼만 기존 상품을 갱신합니다. 일시정지는 현재 요청/상품 완료 후 적용되며 Retry-After 대기 중에는 즉시 멈추지 않을 수 있습니다. 취소는 현재 네트워크 요청 이후 결과 저장을 중단합니다. 취소 작업은 재개하지 않으며 새 작업을 등록하면 기존 저장 상품은 건너뜁니다.

이미지 저장 실패는 상품을 부분 수집 상태로 만들고 로그에 기록합니다. 이미지 실패만 재시도하려면 상품별 재수집을 사용하세요. 파일은 허용된 AliExpress HTTPS CDN에서만 받으며 리다이렉트·15MB 초과·과대 해상도는 거부합니다. 이미지 파일은 JPEG로 재인코딩하며 API 원본 URL도 저장합니다. 상품 삭제 시 DB와 옵션·이미지 메타데이터는 삭제하지만 파일은 유지합니다. 필요 없는 이미지 정리는 추후 기능입니다. 상세 HTML은 실행하지 않고 텍스트로 표시합니다.

SQLite WAL과 인덱스, 서버 페이지네이션을 사용합니다. UI의 실시간 지표는 검색/상세 단계 기반이며 시간 예측은 아닙니다. 로그 화면은 최근 200개, 작업 화면은 최근 50개를 표시하고 전체 이력은 DB에 남습니다. 단일 PC / **단일 FastAPI worker**용입니다. 다중 worker는 지원하지 않습니다.

백업하려면 프로그램을 종료한 뒤 `data` 폴더 전체를 복사하세요. PostgreSQL 전환 시 `DATABASE_URL`을 변경하고 psycopg 드라이버를 추가할 수 있도록 DB 접근을 SQLAlchemy로 분리했습니다. 기존 데이터 마이그레이션은 자동 제공하지 않으며 운영 확장 시 Alembic 및 별도 worker 락이 필요합니다. 상품의 JSON 원본과 공급자 어댑터는 향후 검수·필터·상품명 편집·마켓 등록 계층과 분리되어 있습니다.

## 개발 및 테스트

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
# 별도 터미널
cd frontend
npm install
npm run dev
```

```powershell
# 프로젝트 루트
.venv\Scripts\python -m pytest backend/tests -q
cd frontend
npm run typecheck
npm run build
```

자동 테스트는 별도 임시 SQLite를 사용합니다. 실제 네트워크 없이 옵션 조합 보존·중복 방지·작업 재시작·실패 재시도·CSV/Excel·누락 필드·요청 오류를 검증합니다. `TEST_REPORT.md`에 이번 실행의 확인 결과와 미검증 범위를 기록합니다.

소스를 수정한 뒤 배포용 빌드를 갱신하려면 `frontend`에서 `npm run build`를 실행하세요. `start.bat`은 기존 `.next/BUILD_ID`가 있으면 재사용합니다. 네트워크 설치 오류는 인터넷과 프록시를 확인하고 다시 실행하세요. 500 오류는 `data/logs`를 확인하세요.

"""Official API adapters. No scraping, inferred combinations, or translated options."""
import asyncio
import hashlib
import hmac
import os
import random
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlparse
import httpx

class ProviderError(Exception):
    pass

def array(value, *keys):
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
    if value is None:
        return []
    return value if isinstance(value, list) else [value]

def text_id(value):
    return str(value) if value is not None else None

def retry_delay(header, attempt):
    if header:
        try:
            return max(0, float(header))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(header) - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError):
                pass
    return min(60, 2 ** attempt) + random.random()

class DescriptionImages(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            attrs = dict(attrs)
            url = attrs.get('src') or attrs.get('data-src')
            if url:
                self.urls.append('https:' + url if url.startswith('//') else url)

def affiliate_product(p, currency):
    # Prices and currencies are selected as a pair. Never label a fallback USD price KRW.
    price = p.get('target_original_price')
    unit = p.get('target_original_price_currency')
    if price is None:
        price, unit = p.get('original_price'), p.get('original_price_currency')
    sale = p.get('target_sale_price')
    sale_unit = p.get('target_sale_price_currency')
    if sale is None:
        sale, sale_unit = p.get('sale_price'), p.get('sale_price_currency')
    images = []
    if p.get('product_main_image_url'):
        images.append({'role': 'main', 'url': p['product_main_image_url']})
    for url in array(p.get('product_small_image_urls'), 'string'):
        if url and url != p.get('product_main_image_url'):
            images.append({'role': 'additional', 'url': url})
    return dict(external_id=text_id(p.get('product_id')), url=p.get('product_detail_url'),
                title=p.get('product_title'), seller_name=p.get('shop_name'), seller_id=text_id(p.get('shop_id')),
                category=p.get('second_level_category_name') or p.get('first_level_category_name'), brand=None,
                price=price, currency=unit, sale_price=sale, sale_currency=sale_unit,
                description=None, attributes=None, shipping_cost=None, shipping_available=None,
                variants=[], images=images, option_status='unsupported',
                notes=['Affiliate API는 전체 SKU·재고를 보장하지 않습니다.', '배송 견적 API 미연결: 배송비와 배송 가능 여부는 미수집입니다.'],
                raw={'affiliate': p})

def enrich_ds(product, result):
    product['raw']['dropshipping'] = result
    base = result.get('ae_item_base_info_dto') or {}
    store = result.get('ae_store_info') or {}
    product['seller_name'] = store.get('store_name') or product['seller_name']
    product['seller_id'] = text_id(store.get('store_id')) or product['seller_id']
    product['description'] = base.get('detail')
    product['attributes'] = result.get('ae_item_properties')
    media = result.get('ae_multimedia_info_dto') or {}
    for url in (media.get('image_urls') or '').split(';'):
        if url:
            product['images'].append({'role': 'additional', 'url': url})
    parser = DescriptionImages()
    parser.feed(product['description'] or '')
    product['images'].extend({'role': 'description', 'url': url} for url in parser.urls)
    rows = array(result.get('ae_item_sku_info_dtos'), 'ae_item_sku_info_d_t_o')
    variants = []
    for row in rows:
        props = row.get('ae_sku_property_dtos', row.get('aeop_s_k_u_propertys'))
        props = array(props, 'ae_sku_property_d_t_o')
        options = [{'name': p.get('sku_property_name'), 'value': p.get('property_value_definition_name') or p.get('sku_property_value'),
                    'property_id': p.get('sku_property_id'), 'value_id': p.get('property_value_id') } for p in props]
        image = next((p.get('sku_image') for p in props if p.get('sku_image')), None)
        variants.append({'sku_id': text_id(row.get('sku_id')), 'options': options,
                         'combination': row.get('sku_attr', row.get('id')), 'price': row.get('sku_price'),
                         'currency': row.get('currency_code') or base.get('currency_code'),
                         'stock': row.get('sku_available_stock'), 'image_url': image,
                         'available': row.get('available'), 'raw': row})
        if image:
            product['images'].append({'role': 'option', 'url': image})
    product['variants'] = variants
    product['option_status'] = 'partial'
    product['notes'].append('반환된 SKU 행만 저장했습니다. API 응답만으로 웹사이트 전체 옵션과 원문 언어 일치 여부를 검증할 수 없어 부분 수집입니다.')
    return product

class OfficialProvider:
    def __init__(self):
        self.last_call = 0.0
        self.key = os.getenv('ALI_APP_KEY', '')
        self.secret = os.getenv('ALI_APP_SECRET', '')
        self.gateway = os.getenv('ALI_GATEWAY', 'https://eco.taobao.com/router/rest')
        host = urlparse(self.gateway).hostname or ''
        if urlparse(self.gateway).scheme != 'https' or not (host.endswith('.aliexpress.com') or host == 'eco.taobao.com'):
            raise ProviderError('공식 HTTPS API 게이트웨이만 허용됩니다.')

    async def call(self, method, business):
        if not self.key or not self.secret:
            raise ProviderError('API Key/Secret 미설정. .env 설정 후 재시작하거나 샘플 모드를 사용하세요.')
        for attempt in range(int(os.getenv('API_MAX_RETRIES', '4')) + 1):
            await asyncio.sleep(max(0, self.last_call + float(os.getenv('API_INTERVAL_SECONDS', '1.5')) - time.monotonic()))
            sync = os.getenv('ALI_PROTOCOL', 'top') == 'sync'
            params = dict(app_key=self.key, method=method, format='json', v='2.0',
                          sign_method='sha256' if sync else 'hmac',
                          timestamp=str(int(time.time() * 1000)) if sync else datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'))
            if os.getenv('ALI_SESSION'):
                params['session'] = os.environ['ALI_SESSION']
            params.update({k: str(v) for k, v in business.items() if v is not None and v != ''})
            message = ''.join(k + params[k] for k in sorted(params))
            params['sign'] = hmac.new(self.secret.encode(), message.encode(), hashlib.sha256 if sync else hashlib.md5).hexdigest().upper()
            self.last_call = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=40) as client:
                    response = await client.post(self.gateway, data=params)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < int(os.getenv('API_MAX_RETRIES', '4')):
                        await asyncio.sleep(retry_delay(response.headers.get('Retry-After'), attempt))
                        continue
                    raise ProviderError(f'API HTTP {response.status_code}: 재시도 횟수 초과')
                if response.status_code >= 400:
                    raise ProviderError(f'API HTTP {response.status_code}: 계정 권한과 게이트웨이를 확인하세요.')
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt < int(os.getenv('API_MAX_RETRIES', '4')):
                    await asyncio.sleep(retry_delay(None, attempt))
                    continue
                raise ProviderError('API 네트워크 또는 JSON 응답 오류') from exc
            if 'error_response' in data:
                code = str(data['error_response'].get('sub_code') or data['error_response'].get('code'))
                if ('limit' in code.lower() or code in ('7', '15')) and attempt < int(os.getenv('API_MAX_RETRIES', '4')):
                    await asyncio.sleep(retry_delay(None, attempt))
                    continue
                raise ProviderError(f'API 거절 ({code[:100]}): 앱 권한·토큰·요청 설정 확인 필요')
            envelope = data.get(method.replace('.', '_') + '_response')
            if not isinstance(envelope, dict):
                raise ProviderError('지원하지 않는 API 응답 구조: 원본 명세 확인 필요')
            if 'resp_result' in envelope:
                envelope = envelope['resp_result']
                if str(envelope.get('resp_code')) != '200':
                    raise ProviderError(f'API 업무 오류 ({envelope.get("resp_code")}): 검색 또는 상세정보 미수집')
            if envelope.get('success') in (False, 'false'):
                raise ProviderError('Dropshipping API 실패: 권한 또는 상품 제공 여부 확인 필요')
            if envelope.get('rsp_code') is not None and str(envelope['rsp_code']) not in ('0', '200'):
                raise ProviderError(f'Dropshipping API 오류 ({envelope["rsp_code"]}): 상세·SKU 미수집')
            result = envelope.get('result')
            if not isinstance(result, dict):
                raise ProviderError('API result 미제공')
            return result

    async def search(self, config, page):
        result = await self.call('aliexpress.affiliate.product.query', {
            'keywords': config['keyword'], 'page_no': page, 'page_size': config['page_size'],
            'ship_to_country': config['country'], 'target_currency': config['currency'],
            'sort': config['sort'], 'tracking_id': os.getenv('ALI_TRACKING_ID')})
        return array(result.get('products'), 'product')

    async def detail(self, seed, config):
        result = await self.call('aliexpress.affiliate.productdetail.get', {
            'product_ids': seed['product_id'], 'target_currency': config['currency'],
            'country': config['country'], 'tracking_id': os.getenv('ALI_TRACKING_ID')})
        rows = array(result.get('products'), 'product')
        row = next((r for r in rows if str(r.get('product_id')) == str(seed['product_id'])), None)
        if row is None:
            raise ProviderError('요청한 상품 상세정보가 API에서 제공되지 않았습니다.')
        p = affiliate_product(row, config['currency'])
        if os.getenv('ALI_ENABLE_DS', 'false').lower() == 'true':
            try:
                ds = await self.call('aliexpress.ds.product.get', {'product_id': seed['product_id'],
                                      'ship_to_country': config['country'], 'target_currency': config['currency']})
                enrich_ds(p, ds)
            except ProviderError as exc:
                p['notes'].append('SKU 미지원/미수집: ' + str(exc))
        else:
            p['notes'].append('ALI_ENABLE_DS=false: SKU 조회 비활성. 승인된 Dropshipping 권한이 필요합니다.')
        return p

class MockProvider:
    async def search(self, config, page):
        await asyncio.sleep(0.03)
        digest = hashlib.sha256(config['keyword'].encode()).hexdigest()[:8]
        return [{'product_id': f'MOCK-{digest}-{n:04}', 'index': n} for n in range((page-1)*config['page_size']+1, page*config['page_size']+1)]

    async def detail(self, seed, config):
        await asyncio.sleep(0.03)
        n = seed.get('index', 1)
        price = str(1200 + n * 30) if config['currency'] == 'KRW' else '1.25'
        # Deliberately three actual fixture rows, not a 2x3 Cartesian product.
        variants = [{'sku_id': f'{seed["product_id"]}-{i}',
                     'options': [{'name': 'Color', 'value': color}, {'name': 'Size', 'value': size}],
                     'combination': f'Color:{color};Size:{size}', 'price': price, 'currency': config['currency'],
                     'stock': stock, 'image_url': None, 'available': stock > 0, 'raw': None}
                    for i, (color, size, stock) in enumerate([('Black', 'M3', 120), ('Black', 'M4', 0), ('White', 'M5', 48)])]
        return dict(external_id=seed['product_id'], url=None, title=f'[샘플] {config["keyword"]} 스테인리스 육각 볼트 세트 {n:03}',
                    seller_name='샘플 Industrial Supply', seller_id='MOCK-STORE', category='샘플 / Fasteners', brand=None,
                    price=price, currency=config['currency'], sale_price=None, sale_currency=None,
                    description='화면과 수집 동작 검증을 위한 가상 상품입니다. 실제 알리익스프레스 상품이 아닙니다.',
                    attributes=[{'name': 'Material', 'value': 'Stainless Steel'}], shipping_cost=None, shipping_available=None,
                    variants=variants, images=[{'role': 'main', 'url': None}], option_status='complete',
                    notes=['샘플 데이터: 실제 상품·가격·옵션이 아닙니다.', '샘플에 정의한 3개 조합만 저장합니다.'], raw={'mock_seed': seed})

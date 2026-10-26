import asyncio
import hashlib
import io
import uuid
from urllib.parse import urlparse
import httpx
from PIL import Image, ImageDraw
from .config import IMAGES

ALLOWED_SUFFIXES = ('.alicdn.com', '.aliexpress-media.com', '.aliexpress.com')

async def save_image(source, external_id, entry):
    url = entry.get('url')
    token = hashlib.sha256(f'{source}:{external_id}:{url}'.encode()).hexdigest()
    filename = f'{token}.jpg'
    path = IMAGES / filename
    if path.exists():
        return 'images/' + filename
    if source == 'mock':
        img = Image.new('RGB', (640, 480), '#ecf0eb')
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((250, 90, 390, 170), radius=20, fill='#4c6256')
        draw.rectangle((285, 170, 355, 350), fill='#8ba293')
        for y in range(185, 350, 18):
            draw.line((278, y, 362, y+12), fill='#435c4b', width=7)
        draw.text((24, 24), 'MOCK / SAMPLE', fill='#435c4b')
    else:
        parsed = urlparse(url or '')
        if parsed.scheme != 'https' or not any((parsed.hostname or '').endswith(s) for s in ALLOWED_SUFFIXES):
            raise ValueError('허용된 AliExpress HTTPS 이미지 주소가 아니므로 미수집')
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            async with client.stream('GET', url) as response:
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 15 * 1024 * 1024:
                        raise ValueError('이미지 15MB 제한 초과')
                    chunks.append(chunk)
        img = Image.open(io.BytesIO(b''.join(chunks)))
        if img.width * img.height > 25_000_000:
            raise ValueError('이미지 해상도 제한 초과')
        img = img.convert('RGB')
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        await asyncio.to_thread(img.save, temporary, 'JPEG', quality=92)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return 'images/' + filename

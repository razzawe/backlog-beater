"""Bounded downloads from known Steam image hosts only."""
from io import BytesIO
from urllib.parse import urlsplit
import httpx
from PIL import Image

HOSTS = {'cdn.akamai.steamstatic.com', 'cdn.cloudflare.steamstatic.com',
         'shared.akamai.steamstatic.com', 'shared.cloudflare.steamstatic.com',
         'steamcdn-a.akamaihd.net', 'steamstatic.com'}
MAX_BYTES = 2 * 1024 * 1024


def download_cover(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('unsupported_source')
    data = bytearray()
    with httpx.stream('GET', url, timeout=10, follow_redirects=False) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError('image_too_large')
    with Image.open(BytesIO(data)) as image:
        if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 4_000_000:
            raise ValueError('invalid_image')
        image.load()
        # Normalize validated pixels to a consistent JPEG object.
        output = BytesIO()
        image.convert('RGB').save(output, format='JPEG', quality=85)
    content = output.getvalue()
    if len(content) > MAX_BYTES:
        raise ValueError('image_too_large')
    return content

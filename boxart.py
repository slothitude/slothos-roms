"""Box art lazy-fetcher with libretro-thumbnails cache and Pillow placeholder."""
from __future__ import annotations

import asyncio
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageDraw, ImageFont

THUMB_BASE = "https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/Named_Boxarts/{name}.png"
PLACEHOLDER_BG = (40, 30, 60)
PLACEHOLDER_FG = (220, 200, 255)


def _safe_title(title: str) -> str:
    return "".join(c for c in title if c.isalnum() or c in (" ", "-", "_")).strip() or "unknown"


def placeholder_png(title: str, system: str) -> bytes:
    img = Image.new("RGB", (256, 256), PLACEHOLDER_BG)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
        small = ImageFont.truetype("DejaVuSans.ttf", 12)
    except OSError:
        font = ImageFont.load_default()
        small = font
    draw.text((10, 10), system.upper(), fill=PLACEHOLDER_FG, font=small)
    # word wrap title
    max_w = 236
    line, y = "", 110
    words = title.split()
    for w in words:
        trial = (line + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w:
            line = trial
        else:
            draw.text((10, y), line, fill=PLACEHOLDER_FG, font=font)
            y += 24
            line = w
    if line:
        draw.text((10, y), line, fill=PLACEHOLDER_FG, font=font)
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def fetch_and_cache(
    title: str,
    system: str,
    thumb_repo: str,
    cache_dir: Path,
    client: httpx.AsyncClient,
) -> tuple[bytes, str]:
    """Return (png_bytes, status) where status is 'cached'|'missing'."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_title(title)
    cache_file = cache_dir / system / f"{safe}.png"
    if cache_file.exists():
        return cache_file.read_bytes(), "cached"

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    url = THUMB_BASE.format(repo=urllib.parse.quote(thumb_repo), name=urllib.parse.quote(title))
    try:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code == 200 and resp.content[:4] == b"\x89PNG":
            cache_file.write_bytes(resp.content)
            return resp.content, "cached"
    except (httpx.RequestError, httpx.HTTPError):
        pass
    # fallback: placeholder
    png = placeholder_png(title, system)
    cache_file.write_bytes(png)
    return png, "missing"

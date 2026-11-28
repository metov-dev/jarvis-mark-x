"""Постеры для вкладки «Кино» — со страницы фильма на Киного (og:image).

Качаем один раз и храним в папке данных (posters/), дальше — с диска.
Берём только с Киного: ссылка на страницу — из закладки плеера, картинка —
с того же сайта; чужие адреса из страницы не открываем.
"""
from __future__ import annotations

import io
import logging
import re
import threading
import urllib.parse
import zlib
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_BYTES = 4 * 1024 * 1024
_SIZE = (360, 540)
_MAX_PIXELS = 4000 * 6000
_OG = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', re.I)
_HOST = re.compile(r"^kz\d+\.kinogo-films\.lol$")
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_lock = threading.Lock()
_failed: set[str] = set()          # в этом запуске не вышло — не долбим сайт заново


def _dir() -> Path:
    from core.paths import get_data_root
    d = Path(get_data_root()) / "posters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_for(title: str) -> Path:
    key = " ".join((title or "").casefold().replace("ё", "е").split())
    return _dir() / f"{zlib.crc32(key.encode('utf-8')):08x}.jpg"


def cached(title: str) -> Path | None:
    p = path_for(title)
    return p if p.is_file() else None


def _current_mirror(url: str) -> str:
    """Закладка могла остаться на старом зеркале (kz5) — берём нынешнее."""
    from actions.video_player import _KINOGO_HOST
    parts = urllib.parse.urlsplit(url)
    return parts._replace(netloc=_KINOGO_HOST).geturl()


def _kinogo(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and bool(_HOST.match(parts.hostname or ""))


def fetch(title: str, page_url: str) -> Path | None:
    """Постер на диске (скачает при первом разе) или None."""
    hit = cached(title)
    if hit or not title or not page_url or page_url in _failed:
        return hit
    import requests
    from PIL import Image
    try:
        page = _current_mirror(page_url)
        if not _kinogo(page):
            return None
        # Без редиректов: иначе сайт мог бы увести запрос на чужой адрес.
        html = requests.get(page, timeout=10, headers=_UA, allow_redirects=False).text
        m = _OG.search(html)
        img_url = urllib.parse.urljoin(page, m.group(1)) if m else ""
        if not _kinogo(img_url):
            raise ValueError("нет постера на странице")
        r = requests.get(img_url, timeout=15, headers=_UA, stream=True, allow_redirects=False)
        r.raise_for_status()
        data = r.raw.read(_MAX_BYTES + 1, decode_content=True)
        if len(data) > _MAX_BYTES:
            raise ValueError("картинка слишком большая")
        im = Image.open(io.BytesIO(data), formats=["JPEG", "PNG", "WEBP"])
        if im.width * im.height > _MAX_PIXELS:          # «бомба»: маленький файл, огромная картинка
            raise ValueError("картинка слишком большая")
        im.draft("RGB", _SIZE)
        im.thumbnail(_SIZE)
        im = im.convert("RGB")
        out = path_for(title)
        with _lock:
            tmp = out.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=88)
            tmp.replace(out)
        return out
    except Exception as exc:
        _failed.add(page_url)
        logger.info("Постер «%s» не скачался: %s", title, exc)
        return None

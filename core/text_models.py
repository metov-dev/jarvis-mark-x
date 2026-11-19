"""Побочные запросы к Gemini (память, проверка экрана, «глаза») — по лестнице моделей.

Раньше каждый модуль звал одну жёстко прописанную модель. На бесплатном ключе
у gemini-2.5-flash-lite кончилась квота — и память молча умерла: 133 раза
«квота исчерпана» в журнале, ни одного запомненного факта. Остальные модели
в это время отвечали (замер 19.11.2026: 2.5-flash 1.0 с, 3.1-flash-lite 1.5 с,
3-flash 1.8 с, flash-lite-latest 3.8 с).

Здесь: первая модель лестницы, а на квоте / перегрузке / «модели нет» —
следующая. Упавшая модель отдыхает (квота — 10 мин, перегрузка — 1 мин,
«нет такой модели» — сутки), чтобы следующие запросы не бились в ту же стену.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

LITE = (
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash",
    "gemini-flash-lite-latest",
)
SMART = (
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-flash-latest",
)

_QUOTA_SEC = 600.0
_BUSY_SEC = 60.0
_MISSING_SEC = 86400.0

_rest_until: dict[str, float] = {}
_lock = threading.Lock()


class AllModelsDown(RuntimeError):
    """Ни одна модель лестницы не ответила; текст — последняя причина."""


def _rest_for(exc: Exception) -> float:
    """Сколько модели отдыхать после этой ошибки; 0 — ошибка не про модель
    (плохой запрос, нет сети) — лестница тут не поможет."""
    text = str(exc).lower()
    if "resource_exhausted" in text or "quota" in text or "429" in text or "rate limit" in text:
        return _QUOTA_SEC
    if "not_found" in text or "404" in text or "is not found" in text or "not supported" in text:
        return _MISSING_SEC
    if "unavailable" in text or "503" in text or "overloaded" in text or "500" in text or "internal" in text:
        return _BUSY_SEC
    return 0.0


def ladder(models: tuple[str, ...], first: str | None = None) -> list[str]:
    """Порядок попыток: явно заданная модель (из настроек) — первой."""
    order = ([first] if first else []) + [m for m in models if m != first]
    now = time.monotonic()
    with _lock:
        ready = [m for m in order if _rest_until.get(m, 0.0) <= now]
    return ready or order[:1]          # все отдыхают — пробуем первую, вдруг ожила


def call(fn: Callable[[str], Any], models: tuple[str, ...] = LITE, first: str | None = None,
         what: str = "запрос") -> Any:
    """fn(model) → ответ. Ошибка про модель — следующая ступень; любая другая —
    наверх как есть (её лестница не вылечит)."""
    last: Exception | None = None
    for model in ladder(models, first):
        try:
            return fn(model)
        except Exception as exc:
            rest = _rest_for(exc)
            if not rest:
                raise
            last = exc
            with _lock:
                _rest_until[model] = time.monotonic() + rest
            logger.info("%s: %s недоступна (%s) — следующая модель", what, model, str(exc)[:80])
    raise AllModelsDown(str(last) if last else "нет моделей")


def reset() -> None:
    """Для тестов: забыть, какие модели отдыхают."""
    with _lock:
        _rest_until.clear()

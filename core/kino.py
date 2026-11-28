"""Вкладка «Кино»: что смотрю, что хочу посмотреть, что досмотрел.

«Смотрю» — закладки плеера (actions/video_player, watch_progress.json):
название, сезон, серия, где остановился. «Хочу посмотреть», «досмотрел» и
убранное из списка — свой файл kino.json в папке данных. Закладки плеера
этот модуль не меняет: убрать — значит скрыть до следующего просмотра.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()


@dataclass(frozen=True)
class Show:
    title: str
    status: str                 # watching | want | done
    season: int = 0
    episode: int = 0
    position: int = 0           # секунды
    duration: int = 0
    at: float = 0.0             # когда смотрел

    @property
    def is_series(self) -> bool:
        return self.season > 0 and self.episode > 0

    @property
    def progress(self) -> float:
        return min(1.0, self.position / self.duration) if self.duration else 0.0

    def where(self) -> str:
        """«Сезон 1 · Серия 4 · осталось 41 мин»."""
        parts = []
        if self.is_series:
            parts += [f"Сезон {self.season}", f"Серия {self.episode}"]
        if self.duration:
            left = max(0, self.duration - self.position) // 60
            parts.append("только начал" if self.position < 60 else f"осталось {left} мин")
        return " · ".join(parts)


def _key(title: str) -> str:
    return " ".join((title or "").casefold().replace("ё", "е").split())


def _path() -> Path:
    from core.paths import get_data_root
    return Path(get_data_root()) / "kino.json"


def _load() -> dict:
    from core.storage import safe_read_json
    d = safe_read_json(_path(), default={})
    d = d if isinstance(d, dict) else {}
    return {k: dict(d.get(k) or {}) for k in ("want", "done", "hidden")}


def _save(d: dict) -> None:
    from core.storage import atomic_write_json
    atomic_write_json(_path(), d)


def _bookmarks() -> list[dict]:
    from actions import video_player
    with video_player._watch_lock:
        return video_player._watch_history()


def shows() -> list[Show]:
    """Все: сначала «смотрю» (свежие первыми), потом «хочу», потом «досмотрел»."""
    with _lock:
        d = _load()
    out: list[Show] = []
    seen: set[str] = set()
    for b in sorted(_bookmarks(), key=lambda r: -float(r.get("at") or 0)):
        k = _key(b["title"])
        hidden_at = (d["hidden"].get(k) or {}).get("at")
        at = float(b.get("at") or 0)
        if k in seen or k in d["done"] or (hidden_at is not None and at <= float(hidden_at)):
            continue
        seen.add(k)
        out.append(Show(b["title"], "watching", b["season"], b["episode"], b["position"], b["duration"], at))
    for status in ("want", "done"):
        for k, row in sorted(d[status].items(), key=lambda kv: -float(kv[1].get("at", 0))):
            if k not in seen:
                seen.add(k)
                out.append(Show(row.get("title", k), status, at=float(row.get("at", 0))))
    return out


def _set(title: str, status: str | None) -> None:
    """status: want | done | hidden; None — убрать из своих списков."""
    k = _key(title)
    if not k:
        raise ValueError("пустое название")
    with _lock:
        d = _load()
        for s in ("want", "done", "hidden"):
            d[s].pop(k, None)
        if status:
            d[status][k] = {"title": title.strip(), "at": time.time()}
        _save(d)


def want(title: str) -> str:
    _set(title, "want")
    return f"Добавил «{title}» в «Хочу посмотреть»."


def done(title: str) -> str:
    _set(title, "done")
    return f"«{title}» — в «Досмотрел»."


def remove(title: str) -> str:
    """Убрать из списка. Если начнёт смотреть снова — вернётся в «Смотрю»."""
    _set(title, "hidden")
    return f"Убрал «{title}» из списка."


def kino_tool(p: dict) -> str:
    """Голосом: «добавь Дюну в хочу посмотреть», «я досмотрел Менталиста»,
    «убери Игру престолов из списка», «что я смотрю»."""
    p = p or {}
    action = (p.get("action") or "list").lower()
    title = (p.get("title") or "").strip()
    if action in ("want", "done", "remove"):
        if not title:
            return "Какой фильм или сериал?"
        return {"want": want, "done": done, "remove": remove}[action](title)
    items = shows()
    if not items:
        return "Списки пусты: ничего не смотрю, ничего не отложено."
    groups = {"watching": "Смотрите", "want": "Хотите посмотреть", "done": "Досмотрели"}
    lines = []
    for status, name in groups.items():
        part = [s.title + (f" ({s.where()})" if s.where() else "") for s in items if s.status == status]
        if part:
            lines.append(f"{name}: " + "; ".join(part[:6]))
    return ". ".join(lines) + "."

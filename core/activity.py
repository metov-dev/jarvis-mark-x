"""Что владелец смотрит и слушает — сам, без «посмотри» (TASK-21).

Раньше Джарвис знал только то, что включил сам: фильм, включённый руками,
для него не существовал. Теперь раз в несколько секунд он смотрит в медиа-
сессии Windows (что играет и где) и в заголовок переднего окна. То, что
играет дольше MIN_WATCH_SEC, попадает в короткую память «недавно» (файл,
переживает перезапуск). Скриншоты не делаются, в облако ничего не уходит:
Gemini видит только итоговую строку в подсказке и по инструменту.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

MIN_WATCH_SEC = 120          # трейлер и реклама — не «смотрю»
KEEP = 8                     # сколько помнить «недавно»
FORGET_SEC = 3 * 24 * 3600   # дольше трёх дней — уже не «недавно»

_MUSIC_APPS = ("spotify", "music", "yandexmusic", "zune", "vlc", "foobar", "aimp", "winamp")
_EPISODE = re.compile(r"(\d+)\s*сезон\D{0,12}?(\d+)\s*сери|сезон\s*(\d+)\D{0,12}?сери[яи]\s*(\d+)",
                      re.IGNORECASE)
_SITE_TAIL = re.compile(r"\s*(?:[-—|–]\s*)?(?:смотреть онлайн.*|kinogo.*|youtube|яндекс браузер|"
                        r"yandex browser|google chrome|microsoft edge)\s*$", re.IGNORECASE)


@dataclass
class Activity:
    kind: str            # "video" | "music"
    app: str
    title: str
    artist: str = ""
    season: int | None = None
    episode: int | None = None
    started: float = 0.0     # time.time()
    last_seen: float = 0.0   # time.time()

    def key(self) -> tuple:
        return self.kind, self.title.casefold(), self.season, self.episode

    def label(self) -> str:
        text = f"«{self.title}»"
        if self.season and self.episode:
            text += f", сезон {self.season}, серия {self.episode}"
        if self.artist:
            text += f" — {self.artist}"
        return text


def _kind(app: str) -> str:
    low = app.casefold()
    return "music" if any(m in low for m in _MUSIC_APPS) else "video"


def _clean_title(title: str) -> str:
    return _SITE_TAIL.sub("", (title or "").strip()).strip(" -—|–")[:120]


def _episode(*texts: str) -> tuple[int | None, int | None]:
    for text in texts:
        m = _EPISODE.search(text or "")
        if m:
            a, b, c, d = m.groups()
            return int(a or c), int(b or d)
    return None, None


def from_media(media, window_title: str = "") -> Activity | None:
    """Активность по медиа-сессии Windows (и заголовку окна для сезона/серии)."""
    if not media or not getattr(media, "playing", False):
        return None
    title = _clean_title(media.title) or _clean_title(window_title)
    if not title:
        return None
    season, episode = _episode(media.title, window_title)
    return Activity(_kind(media.app), media.app or "", title, (media.artist or "")[:80], season, episode)


class ActivityTracker:
    def __init__(self, path: Path | None = None, now=time.time):
        self._path = Path(path) if path else None
        self._now = now
        self._lock = threading.Lock()
        self.current: Activity | None = None
        self.recent: list[Activity] = []
        self._load()

    def _load(self):
        if not self._path:
            return
        try:
            from core.storage import safe_read_json
            rows = safe_read_json(self._path, default=[])
            now = self._now()
            self.recent = [Activity(**r) for r in rows if isinstance(r, dict)
                           and now - float(r.get("last_seen", 0)) <= FORGET_SEC][:KEEP]
        except (OSError, TypeError, ValueError):
            self.recent = []

    def _save(self):
        if not self._path:
            return
        try:
            from core.storage import atomic_write_json
            atomic_write_json(self._path, [asdict(a) for a in self.recent])
        except OSError:
            pass

    def observe(self, activity: Activity | None) -> Activity | None:
        """Новое наблюдение. Возвращает активность, только что ставшую «смотрю/слушаю»."""
        now = self._now()
        with self._lock:
            cur = self.current
            if activity is None:
                self.current = None
                return None
            if cur and cur.key() == activity.key():
                cur.last_seen = now
                activity = cur
            else:
                activity.started = activity.last_seen = now
                self.current = activity
            if now - activity.started < MIN_WATCH_SEC:
                return None
            fresh = not self.recent or self.recent[0].key() != activity.key()
            self.recent = [activity] + [a for a in self.recent if a.key() != activity.key()][:KEEP - 1]
            if fresh or now - getattr(self, "_saved_at", 0) >= 60:
                self._saved_at = now
                self._save()
            return activity if fresh else None

    def describe(self) -> str:
        now = self._now()
        with self._lock:
            lines = []
            cur = self.current
            if cur:
                mins = int((now - cur.started) // 60)
                what = "смотрит" if cur.kind == "video" else "слушает"
                lines.append(f"Сейчас владелец {what} {cur.label()} ({cur.app or 'приложение'}, {mins} мин).")
            past = [a for a in self.recent if not cur or a.key() != cur.key()][:3]
            if past:
                lines.append("Недавно: " + "; ".join(
                    f"{a.label()} ({_ago(now - a.last_seen)})" for a in past) + ".")
            return " ".join(lines)


def _ago(sec: float) -> str:
    if sec < 3600:
        return f"{max(1, int(sec // 60))} мин назад"
    if sec < 86400:
        return f"{int(sec // 3600)} ч назад"
    return f"{int(sec // 86400)} дн назад"

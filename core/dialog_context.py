"""Диалоговый контекст: естественные связки реплик (как у Siri и Алисы).

Позволяет помощнику понимать контекстные уточнения, эллиптические фразы
и команды изменения предыдущего действия без ожидания облачной модели:
  • «Какая погода в Москве?» → «В Москве +15»
    «А завтра?» → прогноз для Москвы на завтра.
    «А в Ташкенте?» → погода в Ташкенте.
  • «Поставь таймер на 10 минут» → «Поставил таймер на 10 минут»
    «Сделай 15» / «Переделай на 15 минут» → переставляет текущий таймер.
    «Отмени» / «Хватит» → отменяет текущий таймер.
    «Сколько осталось?» → статус активного таймера.
    «Добавь 5 минут» → увеличивает время таймера.
  • «Сделай тише» → убавил звук.
    «Ещё» / «Чуть-чуть» → убавляет звук ещё раз.
  • Музыка:
    «Дальше» / «Не то» → переключает на следующий трек.
    «Кто поёт?» → что сейчас играет.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.quick import Quick

logger = logging.getLogger(__name__)

# Сколько секунд контекст остаётся активным для уточнений
DEFAULT_TTL_SEC = 60.0


@dataclass
class TurnRecord:
    heard: str
    tool: str | None
    action: str
    args: dict = field(default_factory=dict)
    reply: str = ""
    reply_key: str = ""
    timestamp: float = 0.0


def _normalize_duration_str(d: str) -> str:
    """Если названо просто число без единиц измерения («15», «на 10») — это минуты."""
    s = d.strip()
    if s.isdigit():
        return f"{s} минут"
    words = {"один": "1 минута", "два": "2 минуты", "три": "3 минуты", "пять": "5 минут",
             "десять": "10 минут", "пятнадцать": "15 минут", "двадцать": "20 минут",
             "тридцать": "30 минут", "полчаса": "30 минут"}
    return words.get(s, s)


class DialogContext:
    def __init__(self, ttl: float = DEFAULT_TTL_SEC):
        self.ttl = ttl
        self._lock = threading.Lock()
        self._last_turn: TurnRecord | None = None

    def record(self, heard: str, tool: str | None, args: dict | None = None,
               reply: str = "", reply_key: str = "", timestamp: float | None = None):
        """Запоминает результат последней команды для контекстных уточнений."""
        args = args or {}
        act = str(args.get("action") or "")
        ts = timestamp if timestamp is not None else time.monotonic()
        with self._lock:
            self._last_turn = TurnRecord(
                heard=(heard or "").strip(),
                tool=tool,
                action=act,
                args=dict(args),
                reply=reply or "",
                reply_key=reply_key or "",
                timestamp=ts,
            )

    def get_last(self, now: float | None = None) -> TurnRecord | None:
        now = now if now is not None else time.monotonic()
        with self._lock:
            if self._last_turn and (now - self._last_turn.timestamp <= self.ttl):
                return self._last_turn
            return None

    def clear(self):
        with self._lock:
            self._last_turn = None

    def resolve(self, text: str, now: float | None = None) -> "Quick | None":
        """Пытается понять фразу из контекста предыдущего разговора."""
        from core.quick import Quick

        t = (text or "").strip().lower()
        if not t:
            return None

        last = self.get_last(now)
        if not last:
            return None

        # ── 1. Контекст погоды ───────────────────────────────────────────────
        if last.tool == "weather":
            city = str(last.args.get("city") or "")
            # «а завтра?», «на завтра», «что завтра», «какая завтра», «завтра»
            if re.fullmatch(r"(?:а |и |что |какая )?(?:на )?завтра(?:\s+как|\s+что)?", t):
                return Quick("weather", {"city": city, "day": "tomorrow"}, "", t)

            # «послезавтра», «а послезавтра»
            if re.fullmatch(r"(?:а |и )?послезавтра", t):
                return Quick("weather", {"city": city, "day": "day_after"}, "", t)

            # «на выходных», «на выходные», «в субботу», «в воскресенье»
            if re.fullmatch(r"(?:а |и )?(?:на )?(?:выходны[хе]|субботу|воскресенье)", t):
                return Quick("weather", {"city": city, "day": "weekend"}, "", t)

            # «а в питере?», «а в ташкенте?», «в москве»
            m_city = re.fullmatch(r"(?:а |и )?(?:в |во )(?P<city>[а-яa-z -]{2,30})(?:\s+как|\s+что)?", t)
            if m_city:
                new_city = m_city.group("city").strip()
                if new_city not in ("выходные", "выходных", "субботу", "воскресенье"):
                    return Quick("weather", {"city": new_city}, "", t)

        # ── 2. Контекст таймера ───────────────────────────────────────────────
        is_timer_ctx = (
            last.tool == "clock" and (
                last.action in ("timer_set", "timer_add", "timer_list", "timer_modify",
                                "timer_pause", "timer_resume")
                or "таймер" in last.reply.lower()
                or "таймер" in last.heard.lower()
            )
        )
        if is_timer_ctx:
            # «отмени», «сбрось», «удали», «хватит», «не надо», «стоп»
            if re.fullmatch(r"(?:отмени|сбрось|удали|выключи|останови)(?: его| этот таймер)?|хватит|не надо", t):
                return Quick("clock", {"action": "timer_cancel"}, "", t)

            # «переделай на 15», «сделай 15», «поменяй на 10 минут»
            m_mod = re.fullmatch(r"(?:переделай|поменяй|сделай|переставь|поставь)(?: теперь)? (?:на )?(?P<d>.+)", t)
            if m_mod:
                dur = _normalize_duration_str(m_mod.group("d"))
                return Quick("clock", {"action": "timer_modify", "duration": dur}, "", t)

            # «добавь 5 минут», «прибавь 10 минут», «накинь 5 минут», «плюс 5 минут»
            m_add = re.fullmatch(r"(?:добавь|прибавь|накинь)(?: еще| ещё)? (?P<d>.+)|плюс (?P<d2>.+)", t)
            if m_add:
                raw_d = m_add.group("d") or m_add.group("d2")
                dur = _normalize_duration_str(raw_d)
                return Quick("clock", {"action": "timer_add", "duration": dur}, "", t)

            # «сколько осталось?», «сколько там?», «сколько еще?»
            if re.fullmatch(r"(?:а )?сколько (?:там |еще |ещё )?(?:осталось|времени)", t):
                return Quick("clock", {"action": "timer_list"}, "", t)

            # Пауза / возобновление
            if re.fullmatch(r"пауза|приостанови(?: его)?|(?:поставь )?на паузу", t):
                return Quick("clock", {"action": "timer_pause"}, "", t)
            if re.fullmatch(r"продолжи|возобнови|сними с паузы", t):
                return Quick("clock", {"action": "timer_resume"}, "", t)

        # ── 3. Контекст громкости ─────────────────────────────────────────────
        is_volume_ctx = (
            (last.tool in ("computer_control", "music_player", "video_control"))
            and last.action in ("volume_up", "volume_down")
        )
        if is_volume_ctx:
            # «ещё», «чуть-чуть», «немного», «ещё раз», «повтори»
            if re.fullmatch(r"ещ[её](?: раз| немного| чуть[- ]чуть)?|чуть[- ]чуть|немного|повтори|давай ещ[её]", t):
                reply_key = "louder" if last.action == "volume_up" else "quieter"
                return Quick(last.tool, {"action": last.action}, reply_key, t)

            # «хватит», «нормально», «достаточно», «хорошо», «пойдёт»
            if re.fullmatch(r"хватит|нормально|достаточно|хорошо|пойдет", t):
                return Quick(None, {}, "ack", t)

        # Короткие реплики о сериях привязаны к последней команде о сериале.
        if last.tool == "movie_player" and (
            last.args.get("season") is not None or last.action.startswith("series_")
        ):
            title = str(last.args.get("title") or "")
            if re.fullmatch(r"(?:а |и )?(?:дальше|следующую|следующая)", t):
                return Quick("movie_player", {"action": "series_next", "title": title}, "", t)
            if re.fullmatch(r"(?:а |и )?(?:с начала|сначала)", t):
                return Quick("movie_player", {"action": "series_restart", "title": title}, "", t)

        # ── 4. Контекст музыки / плеера ───────────────────────────────────────
        if last.tool == "music_player":
            # «дальше», «следующую», «не то», «другую»
            if re.fullmatch(r"дальше|следующ(?:ую|ий|ее)|не то|другую", t):
                return Quick("music_player", {"action": "next"}, "next", t)

            # «назад», «прошлую», «предыдущую», «верни»
            if re.fullmatch(r"назад|прошлую|предыдущую|верни", t):
                return Quick("music_player", {"action": "previous"}, "previous", t)

            # «кто это?», «кто поёт?», «что это за песня?»
            if re.fullmatch(r"кто (?:это|поет|поёт)|что (?:это|за песня|играет)", t):
                return Quick("music_player", {"action": "now_playing"}, "", t)

        return None


_WORK_APPS = {"code.exe", "cursor.exe", "pycharm64.exe", "devenv.exe", "winword.exe", "excel.exe", "powerpnt.exe"}


def foreground_context() -> dict:
    """Сведения о переднем приложении без снимков экрана."""
    from core import win_apps

    snapshot = {}
    window = win_apps.foreground()
    if window and window.exe:
        from pathlib import PureWindowsPath
        app = PureWindowsPath(window.exe).name
        if app.casefold() != "jarvis.exe":
            snapshot["application"] = app
            snapshot["window"] = (window.title or "")[:160]
    return snapshot


def desktop_context() -> dict:
    from core import media_session

    snapshot = foreground_context()
    media = media_session.now_playing()
    if media and media.playing:
        snapshot["playing"] = {"application": media.app, "title": (media.title or "")[:160]}
    return snapshot


class WorkContext:
    """Последнее видимое рабочее окно. Данные остаются в памяти процесса."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last: tuple[str, float] | None = None

    def observe(self, snapshot: dict, now: float) -> None:
        title = snapshot.get("window", "").strip()
        if title and snapshot.get("application", "").casefold() in _WORK_APPS:
            with self._lock:
                self._last = (title, now)

    def describe(self, now: float) -> str:
        with self._lock:
            recent = self._last
        if not recent or not 0 <= now - recent[1] <= 3600:
            return "За последний час я не видел рабочего окна."
        return f"Последнее рабочее окно — «{recent[0]}»."


class ResumeObserver:
    """Отмечает возвращение к рабочему окну после перерыва без озвучки."""

    def __init__(self):
        self._current: tuple[str, str] | None = None
        self._away_since: dict[tuple[str, str], float] = {}

    def observe(self, snapshot: dict, now: float) -> bool:
        app = snapshot.get("application", "").casefold()
        title = snapshot.get("window", "").strip()
        current = (app, title) if app in _WORK_APPS and title else None
        if current == self._current:
            return False
        self._away_since = {key: ts for key, ts in self._away_since.items() if now - ts <= 3600}
        if self._current:
            self._away_since[self._current] = now
        self._current = current
        left = self._away_since.pop(current, None) if current else None
        return left is not None and now - left >= 600


# ── Глобальный синглтон ───────────────────────────────────────────────────────
_context: DialogContext | None = None


def context() -> DialogContext:
    global _context
    if _context is None:
        _context = DialogContext()
    return _context

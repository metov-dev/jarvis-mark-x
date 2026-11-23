"""Часы Джарвиса: таймеры, секундомер, будильники, «который час».

Раньше был только таймер сна; «поставь таймер на 10 минут», «разбуди в 7»,
«засеки время» Джарвис делать не умел, а время брал из промпта, собранного
при подключении, — в долгой сессии оно устаревало.

Всё состояние — в clock.json (время окончания таймеров — по часам стены,
поэтому перезапуск их не теряет). Проверка раз в полсекунды в своём потоке;
в тестах её зовут напрямую (tick) на поддельных часах.

Когда что-то сработало: звук (Windows, файлы из C:\\Windows\\Media), голос
Джарвиса (say — указание в Live-сессию) и событие в журнал и капсулу (notify).
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from functools import wraps
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from core.storage import atomic_write_json

logger = logging.getLogger(__name__)

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
          "октября", "ноября", "декабря"]
REPEATS = {"once": "один раз", "daily": "каждый день", "weekdays": "по будням", "weekends": "по выходным"}
ALARM_RING_SEC = 20.0          # сколько звенит перед тем, как Джарвис заговорит
ALARM_GIVE_UP_SEC = 5 * 60     # никто не выключил — перестать звенеть
ALARM_REPEAT_SEC = 60.0        # пока не выключили — звенеть снова раз в минуту
TIMER_RING_SEC = 6.0


# ── слова ─────────────────────────────────────────────────────────────────────

def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def say_seconds(sec: float) -> str:
    """3725 → «1 час 2 минуты 5 секунд». Для голоса, без нулевых частей."""
    sec = int(round(max(0.0, sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} {plural(h, 'час', 'часа', 'часов')}")
    if m:
        parts.append(f"{m} {plural(m, 'минута', 'минуты', 'минут')}")
    if s or not parts:
        parts.append(f"{s} {plural(s, 'секунда', 'секунды', 'секунд')}")
    return " ".join(parts)


def clock_face(sec: float) -> str:
    """Для капсулы и журнала: 83 → «1:23», 3725 → «1:02:05»."""
    sec = int(max(0.0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


_WORD_NUM = {"одну": 1, "одна": 1, "один": 1, "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5,
             "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "пятнадцать": 15,
             "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "полтора": 1.5,
             "полторы": 1.5}


def parse_duration(p: dict) -> float | None:
    """Секунды из параметров: hours/minutes/seconds числами или duration
    текстом («полчаса», «1:30», «90 секунд», «полторы минуты», «час»)."""
    total, seen = 0.0, False
    for key, k in (("hours", 3600), ("minutes", 60), ("seconds", 1)):
        v = p.get(key)
        if v not in (None, ""):
            try:
                amount = float(v)
            except (TypeError, ValueError):
                return None
            if isinstance(v, bool) or not math.isfinite(amount) or amount < 0:
                return None
            total += amount * k
            seen = True
    if seen:
        return total if math.isfinite(total) and total > 0 else None
    text = str(p.get("duration") or "").lower().replace(",", ".")
    if not text or re.search(r"[-−–]\s*\d|\bминус\b", text):
        return None
    if "полчаса" in text:
        return 1800.0
    m = re.fullmatch(r"\s*(\d+):(\d{2})(?::(\d{2}))?\s*", text)
    if m:                                        # «1:30» — минуты:секунды, «1:30:00» — часы
        a, b, c = int(m[1]), int(m[2]), m[3]
        if b >= 60 or (c is not None and int(c) >= 60):
            return None
        return float(a * 3600 + b * 60 + int(c) if c else a * 60 + b) or None
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?|[а-я]+)?\s*(час|мин|сек)", text):
        n = float(num) if num and num[0].isdigit() else _WORD_NUM.get(num, 1.0)
        total += n * {"час": 3600, "мин": 60, "сек": 1}[unit]
    return total if math.isfinite(total) and total > 0 else None


def parse_hhmm(text: str) -> tuple[int, int]:
    """«7», «07:30», «7.30», «19 30», «7 утра», «8 вечера» → (часы, минуты)."""
    t = str(text or "").lower()
    m = re.search(r"(\d{1,2})(?:[:.\s](\d{2}))?", t)
    if not m:
        raise ValueError(f"не понял время «{text}»")
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if re.search(r"веч|ночи|pm", t) and h < 12 and not ("ночи" in t and h <= 4):
        h += 12
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError(f"не понял время «{text}»")
    return h, mi


# ── данные ────────────────────────────────────────────────────────────────────

@dataclass
class Timer:
    id: int
    label: str
    total: float
    end: float = 0.0               # когда сработает (time.time); 0 — на паузе
    left: float = 0.0              # сколько осталось на паузе


@dataclass
class Alarm:
    id: int
    time: str                      # «07:30»
    label: str = ""
    repeat: str = "once"           # once | daily | weekdays | weekends
    date: str = ""                 # для «один раз»: на какой день
    last: str = ""                 # день, когда уже звенел
    snooze_until: float = 0.0


@dataclass
class Stopwatch:
    started: float = 0.0           # идёт с этого момента (time.time); 0 — стоит
    elapsed: float = 0.0           # набежало до последней остановки
    laps: list = field(default_factory=list)

    def value(self, now: float) -> float:
        return self.elapsed + (max(0.0, now - self.started) if self.started else 0.0)


# ── часы ──────────────────────────────────────────────────────────────────────

class _ClockSaveError(OSError):
    pass


SAVE_FAILED = "Не удалось сохранить изменения в часах, сэр."


def _persist_action(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self._lock:
            ringing = self._ringing
            previous = deepcopy((self.timers, self.alarms, self.stopwatch, self._next_id, ringing))
            try:
                return method(self, *args, **kwargs)
            except _ClockSaveError as exc:
                self.timers, self.alarms, self.stopwatch, self._next_id, previous_ring = previous
                if ringing and previous_ring:
                    vars(ringing).update(vars(previous_ring))
                    self.alarms = [ringing if alarm is previous_ring else alarm for alarm in self.alarms]
                self._ringing = ringing
                logger.warning("clock.json не сохранён: %s", exc)
                return SAVE_FAILED
    return run


class Clock:
    def __init__(self, path: str | Path | None = None, now: Callable[[], float] = time.time,
                 local: Callable[[], datetime] | None = None):
        self.path = Path(path) if path else None
        self.now = now
        self.local = local or (lambda: datetime.fromtimestamp(self.now()))
        self.say: Callable[[str], None] = lambda text: None
        self.notify: Callable[[str, str], None] = lambda title, text: None
        self.on_tick: Callable[[], None] | None = None
        self._stopwatch_saved_at = 0.0
        self.sound = Sound()
        self._lock = threading.RLock()
        self.timers: list[Timer] = []
        self.alarms: list[Alarm] = []
        self.stopwatch = Stopwatch()
        self._next_id = 1
        self._retry_save_at: float | None = None
        self._ringing: Alarm | None = None
        self._ring_started = 0.0
        self._ring_spoken = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._load()

    # ── хранение ──────────────────────────────────────────────────────────────
    def _load(self):
        if not self.path or not self.path.is_file():
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            timers = [Timer(**t) for t in d.get("timers", [])]
            alarms = [Alarm(**a) for a in d.get("alarms", [])]
            stopwatch = Stopwatch(**d.get("stopwatch", {}))
            if stopwatch.started:
                # Старый формат не содержит времени последней записи секундомера.
                stopwatch.elapsed = max([stopwatch.elapsed, *stopwatch.laps])
                stopwatch.started = 0.0
            latest_id = max((record.id for record in [*timers, *alarms]), default=0)
            self._next_id = max(int(d.get("next_id", 1)), latest_id + 1)
            self.timers, self.alarms, self.stopwatch = timers, alarms, stopwatch
        except Exception as exc:
            logger.warning("clock.json не читается: %s", exc)

    def _save(self):
        if not self.path:
            return
        saved_at = self.now()
        stopwatch = asdict(self.stopwatch)
        stopwatch.update(started=0.0, elapsed=self.stopwatch.value(saved_at))
        data = {"timers": [asdict(t) for t in self.timers], "alarms": [asdict(a) for a in self.alarms],
                "stopwatch": stopwatch, "next_id": self._next_id}
        try:
            atomic_write_json(self.path, data)
        except OSError as exc:
            raise _ClockSaveError(str(exc)) from exc
        self._retry_save_at = None
        self._stopwatch_saved_at = saved_at

    def _save_after_tick(self):
        # Уже сработавший таймер не должен повторяться каждые полсекунды при сбое записи.
        try:
            self._save()
        except _ClockSaveError as exc:
            self._retry_save_at = time.monotonic() + 5
            logger.warning("Не удалось сохранить срабатывание часов: %s", exc)

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id - 1

    # ── время ─────────────────────────────────────────────────────────────────
    def now_text(self) -> str:
        d = self.local()
        return (f"Сейчас {d:%H:%M}, {WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]} "
                f"{d.year} года.")

    # ── таймеры ───────────────────────────────────────────────────────────────
    def _find_timers(self, which: str, allow_all: bool = True) -> tuple[list[Timer], str]:
        w = (which or "").strip().casefold()
        if not self.timers:
            return [], "Таймеров нет."
        if not w:
            return self.timers[-1:], ""
        if allow_all and w in ("все", "всё", "all"):
            return list(self.timers), ""
        number = re.fullmatch(r"(?:номер\s+|#)?(\d+)", w)
        by_id = [t for t in self.timers if number and number[1] == str(t.id)]
        exact = [t for t in self.timers if w == t.label.casefold()]
        matches = by_id or exact or [t for t in self.timers if w in t.label.casefold()]
        if len(matches) > 1:
            options = ", ".join(f"«{t.label}», номер {t.id}" for t in matches)
            return [], f"Уточните таймер: {options}."
        if not matches:
            return [], f"Таймер «{which}» не найден."
        return matches, ""

    @_persist_action
    def timer_set(self, seconds: float | None, label: str = "") -> str:
        if not seconds or not math.isfinite(seconds) or seconds <= 0:
            return "Укажите длительность таймера, сэр."
        t = Timer(self._id(), (label or "").strip(), float(seconds), self.now() + float(seconds))
        self.timers.append(t)
        self._save()
        return f"Таймер{(' «' + t.label + '»') if t.label else ''} на {say_seconds(seconds)} запущен."

    def timer_left(self, t: Timer) -> float:
        return t.left if not t.end else max(0.0, t.end - self.now())

    def timer_list(self) -> str:
        with self._lock:
            if not self.timers:
                return "Таймеров нет."
            parts = []
            for t in sorted(self.timers, key=self.timer_left):
                name = f"«{t.label}»" if t.label else f"на {say_seconds(t.total)}"
                state = " (на паузе)" if not t.end else ""
                parts.append(f"{name} — осталось {say_seconds(self.timer_left(t))}{state}")
        return "Таймеры: " + "; ".join(parts) + "."

    @_persist_action
    def timer_cancel(self, which: str = "") -> str:
        gone, error = self._find_timers(which)
        if error:
            return error
        self.timers = [t for t in self.timers if t not in gone]
        self._save()
        return "Все таймеры отменены." if len(gone) > 1 else "Таймер отменён."

    @_persist_action
    def timer_modify(self, seconds: float | None, which: str = "") -> str:
        if not seconds or not math.isfinite(seconds) or seconds <= 0:
            return "Укажите новую длительность таймера, сэр."
        timers, error = self._find_timers(which, allow_all=False)
        if error:
            return error
        t = timers[0]
        t.total, t.end, t.left = seconds, self.now() + seconds, 0.0
        self._save()
        return f"Таймер переставлен на {say_seconds(seconds)}."

    @_persist_action
    def timer_add(self, seconds: float | None, which: str = "") -> str:
        ts, error = self._find_timers(which, allow_all=False)
        if error:
            return error
        if not seconds or not math.isfinite(seconds) or seconds <= 0:
            return "Укажите, на сколько продлить таймер, сэр."
        t = ts[0]
        if t.end:
            t.end += seconds
        else:
            t.left += seconds
        t.total += seconds
        self._save()
        return f"Добавил {say_seconds(seconds)}. Осталось {say_seconds(self.timer_left(t))}."

    @_persist_action
    def timer_pause(self, which: str = "") -> str:
        ts, error = self._find_timers(which)
        if error:
            return error
        ts = [t for t in ts if t.end]
        if not ts:
            return "Нечего ставить на паузу."
        now = self.now()
        for t in ts:
            t.left, t.end = max(0.0, t.end - now), 0.0
        self._save()
        return "Таймер на паузе."

    @_persist_action
    def timer_resume(self, which: str = "") -> str:
        ts, error = self._find_timers(which)
        if error:
            return error
        ts = [t for t in ts if not t.end]
        if not ts:
            return "Нечего продолжать."
        now = self.now()
        for t in ts:
            t.end, t.left = now + t.left, 0.0
        self._save()
        return "Таймер продолжен."

    # ── секундомер ────────────────────────────────────────────────────────────
    @_persist_action
    def sw_start(self) -> str:
        sw = self.stopwatch
        if sw.started:
            return f"Секундомер уже идёт: {say_seconds(sw.value(self.now()))}."
        sw.started = self.now()
        self._save()
        return "Секундомер пошёл." if not sw.elapsed else "Секундомер продолжен."

    @_persist_action
    def sw_stop(self) -> str:
        sw = self.stopwatch
        if not sw.started:
            return "Секундомер не запущен." if not sw.elapsed else \
                f"Секундомер уже стоит: {say_seconds(sw.elapsed)}."
        sw.elapsed, sw.started = sw.value(self.now()), 0.0
        self._save()
        return f"Стоп. {say_seconds(sw.elapsed)}."

    @_persist_action
    def sw_lap(self) -> str:
        sw = self.stopwatch
        if not sw.started:
            return "Секундомер не запущен."
        v = sw.value(self.now())
        sw.laps.append(v)
        self._save()
        prev = sw.laps[-2] if len(sw.laps) > 1 else 0.0
        return f"Круг {len(sw.laps)}: {say_seconds(v - prev)} (всего {say_seconds(v)})."

    @_persist_action
    def sw_reset(self) -> str:
        self.stopwatch = Stopwatch()
        self._save()
        return "Секундомер сброшен."

    def sw_status(self) -> str:
        sw = self.stopwatch
        if not sw.started and not sw.elapsed:
            return "Секундомер не запущен."
        v = say_seconds(sw.value(self.now()))
        return f"На секундомере {v}" + ("" if sw.started else " (стоит)") + \
            (f", кругов: {len(sw.laps)}." if sw.laps else ".")

    # ── будильники ────────────────────────────────────────────────────────────
    def next_ring(self, a: Alarm, after: datetime | None = None) -> datetime | None:
        """Ближайший звонок будильника (местное время) не раньше after."""
        after = after or self.local()
        h, m = parse_hhmm(a.time)
        if a.snooze_until:
            return datetime.fromtimestamp(a.snooze_until)
        if a.repeat == "once":
            day = datetime.strptime(a.date, "%Y-%m-%d") if a.date else after
            at = day.replace(hour=h, minute=m, second=0, microsecond=0)
            return at if a.last != at.date().isoformat() else None
        for i in range(8):
            d = (after + timedelta(days=i)).replace(hour=h, minute=m, second=0, microsecond=0)
            if d.date().isoformat() == a.last or (i == 0 and d < after - timedelta(minutes=2)):
                continue
            wd = d.weekday()
            if a.repeat == "daily" or (a.repeat == "weekdays" and wd < 5) or (a.repeat == "weekends" and wd >= 5):
                return d
        return None

    @_persist_action
    def alarm_set(self, when: str, label: str = "", repeat: str = "once") -> str:
        try:
            h, m = parse_hhmm(when)
        except ValueError as exc:
            return f"Сэр, {exc}."
        repeat = repeat if repeat in REPEATS else "once"
        now = self.local()
        a = Alarm(self._id(), f"{h:02d}:{m:02d}", (label or "").strip(), repeat)
        if repeat == "once":
            day = now.date() if (h, m) > (now.hour, now.minute) else now.date() + timedelta(days=1)
            a.date = day.isoformat()
        self.alarms.append(a)
        self._save()
        at = self.next_ring(a, now)
        when_txt = "сегодня" if at and at.date() == now.date() else \
            "завтра" if at and at.date() == now.date() + timedelta(days=1) else \
            (WEEKDAYS[at.weekday()] if at else "")
        left = say_seconds((at - now).total_seconds()) if at else ""
        rep = "" if repeat == "once" else f", {REPEATS[repeat]}"
        return (f"Будильник на {a.time}{rep} — {when_txt}, через {left}."
                + (f" «{a.label}»." if a.label else ""))

    def alarm_list(self) -> str:
        with self._lock:
            if not self.alarms:
                return "Будильников нет."
            parts = [f"{a.time} {REPEATS.get(a.repeat, '')}" + (f" «{a.label}»" if a.label else "")
                     for a in sorted(self.alarms, key=lambda a: a.time)]
        return "Будильники: " + "; ".join(p.replace(" один раз", "") for p in parts) + "."

    @_persist_action
    def alarm_cancel(self, which: str = "") -> str:
        w = (which or "").strip().lower()
        if w in ("все", "всё", "all"):
            gone = list(self.alarms)
        elif w:
            try:
                h, m = parse_hhmm(w)
                key = f"{h:02d}:{m:02d}"
            except ValueError:
                key = None
            number = re.fullmatch(r"(?:номер\s+|#)(\d+)", w)
            if number:
                gone = [a for a in self.alarms if number[1] == str(a.id)]
            else:
                exact = [a for a in self.alarms if a.time == key or a.label.casefold() == w]
                gone = exact or [a for a in self.alarms if w in a.label.casefold()]
            if len(gone) > 1:
                options = ", ".join(f"{a.time} «{a.label}» (номер {a.id})" for a in gone)
                return f"Уточните, какой будильник удалить: {options}."
        else:
            gone = self.alarms[-1:]
        if not gone:
            return "Такого будильника нет."
        self.alarms = [a for a in self.alarms if a not in gone]
        was_ringing = self._ringing in gone
        self._save()
        if was_ringing:
            self._stop_ringing()
        return "Все будильники удалены." if len(gone) > 1 else f"Будильник на {gone[0].time} удалён."

    @_persist_action
    def alarm_stop(self) -> str:
        if not self._ringing:
            return "Будильник не звенит."
        self._clear_ringing()
        self._save()
        self.sound.stop()
        return "Будильник выключен. Доброе утро, сэр."

    @_persist_action
    def alarm_snooze(self, minutes: float = 5) -> str:
        minutes = float(minutes or 5)
        if not math.isfinite(minutes) or minutes <= 0:
            return "Укажите, на сколько отложить будильник, сэр."
        a = self._ringing
        if not a:
            return "Будильник не звенит."
        self._clear_ringing(done=False)
        a.snooze_until = self.now() + minutes * 60
        self._save()
        self.sound.stop()
        return f"Хорошо, разбужу ещё раз через {say_seconds(minutes * 60)}."

    def _stop_ringing(self):
        self._clear_ringing()
        self.sound.stop()

    def _clear_ringing(self, done: bool = True):
        a = self._ringing
        self._ringing = None
        if a and done:
            a.snooze_until = 0.0
            if a.repeat == "once":
                self.alarms = [x for x in self.alarms if x is not a]

    # ── капсула ───────────────────────────────────────────────────────────────
    def nearest(self) -> tuple[str, float]:
        """(подпись, время окончания для обратного отсчёта или 0)."""
        with self._lock:
            if self._ringing:
                return f"Будильник {self._ringing.time}", 0.0
            now = self.now()
            running = [t for t in self.timers if t.end and t.end > now]
            if running:
                t = min(running, key=lambda t: t.end)
                return (t.label or "Таймер"), t.end
            if self.stopwatch.started:
                return f"Секундомер · {clock_face(self.stopwatch.value(self.now()))}", 0.0
            # Ближайший будильник в капсуле не показываем: владелец — «этот текст
            # бесполезен». Видно только то, что идёт сейчас: таймер, секундомер,
            # звенящий будильник.
        return "", 0.0

    # ── ход часов ─────────────────────────────────────────────────────────────
    def tick(self):
        now, local = self.now(), self.local()
        fired: list[Timer] = []
        missed: list[Alarm] = []
        with self._lock:
            for t in list(self.timers):
                if t.end and t.end <= now:
                    self.timers.remove(t)
                    fired.append(t)
            ring: Alarm | None = None
            for alarm in list(self.alarms):
                if alarm is self._ringing:
                    continue
                at = self.next_ring(alarm, local)
                delay = (local - at).total_seconds() if at else None
                if (alarm.repeat == "once" and at is None and alarm.last) or (
                    delay is not None and delay >= 120 and (alarm.repeat == "once" or alarm.snooze_until)
                ):
                    missed.append(alarm)
                    if alarm.repeat == "once":
                        self.alarms.remove(alarm)
                    else:
                        alarm.snooze_until = 0.0
                elif not self._ringing and ring is None and delay is not None and 0 <= delay < 120:
                    ring = alarm
            if ring:
                ring.last = local.date().isoformat()
                ring.snooze_until = 0.0
                self._ringing, self._ring_started, self._ring_spoken = ring, now, 0.0
            retry = self._retry_save_at
            checkpoint = self.path and self.stopwatch.started and now - self._stopwatch_saved_at >= 10
            if fired or missed or ring or (retry is not None and time.monotonic() >= retry) or (
                checkpoint and retry is None
            ):
                self._save_after_tick()
        for alarm in missed:
            suffix = f" — {alarm.label}" if alarm.label else ""
            logger.info("Пропущен будильник %s%s", alarm.time, suffix)
            self.notify("ПРОПУЩЕН БУДИЛЬНИК", alarm.time + suffix)
        for t in fired:
            self._timer_fired(t)
        if ring:
            logger.info("Будильник %s «%s» звенит", ring.time, ring.label)
            self.notify("БУДИЛЬНИК", f"{ring.time}" + (f" — {ring.label}" if ring.label else ""))
            self.sound.play("alarm", loop=True)
        self._keep_ringing(now)

    def _timer_fired(self, t: Timer):
        what = f"«{t.label}»" if t.label else f"на {say_seconds(t.total)}"
        logger.info("Таймер %s сработал", what)
        self.notify("ТАЙМЕР", f"Время вышло: {t.label or say_seconds(t.total)}")
        self.sound.play("timer", seconds=TIMER_RING_SEC)
        self.say(f"[СИСТЕМА: сработал таймер {what}. Скажи пользователю одной короткой фразой, "
                 "что время вышло" + (f" ({t.label})" if t.label else "") + ".]")

    def _keep_ringing(self, now: float):
        a = self._ringing
        if not a:
            return
        rang = now - self._ring_started
        if rang > ALARM_GIVE_UP_SEC:
            logger.info("Будильник %s: никто не выключил — перестаю звенеть", a.time)
            with self._lock:
                self._stop_ringing()
                self._save_after_tick()
            return
        # Позвенел — Джарвис говорит сам (звук на это время стихает), и так
        # раз в минуту, пока будильник не выключат или не отложат.
        if rang >= ALARM_RING_SEC and now - self._ring_spoken >= ALARM_REPEAT_SEC:
            self._ring_spoken = now
            self.sound.stop()
            label = f" («{a.label}»)" if a.label else ""
            self.say(f"[СИСТЕМА: звенит будильник на {a.time}{label}, сейчас {self.local():%H:%M}. "
                     "Разбуди пользователя одной-двумя бодрыми фразами и предложи отложить на 5 минут. "
                     "Скажет «отложи» — вызови clock с action=\"alarm_snooze\", «выключи»/«встаю» — "
                     "action=\"alarm_stop\". Сам будильник не выключай.]")
            def resume_sound():
                with self._lock:
                    if self._ringing is a:
                        self.sound.play("alarm", loop=True)

            reminder = threading.Timer(12.0, resume_sound)
            reminder.daemon = True
            reminder.start()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def run():
            while not self._stop.wait(0.5):
                try:
                    self.tick()
                except Exception as exc:
                    logger.warning("Часы: %s", exc)
                if self.on_tick:
                    try:
                        self.on_tick()
                    except Exception as exc:
                        logger.warning("Напоминания: %s", exc)
        self._thread = threading.Thread(target=run, daemon=True, name="clock")
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            if self.stopwatch.started:
                self.sw_stop()
        self._thread = None


class Sound:
    """Звук будильника и таймера. Только Windows (winsound); без него — тишина,
    будит голос Джарвиса."""

    FILES = {"alarm": ("Alarm01.wav", "Alarm02.wav", "Ring01.wav"),
             "timer": ("Alarm02.wav", "Alarm01.wav", "notify.wav")}

    def __init__(self):
        self.playing = False
        self._stop_at: threading.Timer | None = None

    def _file(self, kind: str) -> str | None:
        media = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Media"
        return next((str(media / f) for f in self.FILES[kind] if (media / f).is_file()), None)

    def play(self, kind: str, loop: bool = False, seconds: float = 0.0):
        self.playing = True
        try:
            import winsound
        except ImportError:
            return
        path = self._file(kind)
        try:
            if path:
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                                   | (winsound.SND_LOOP if loop or seconds else 0))
            else:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception as exc:
            logger.debug("Звук %s: %s", kind, exc)
        if seconds:
            self._stop_at = threading.Timer(seconds, self.stop)
            self._stop_at.daemon = True
            self._stop_at.start()

    def stop(self):
        self.playing = False
        if self._stop_at:
            self._stop_at.cancel()
            self._stop_at = None
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except Exception:
            pass


_clock: Clock | None = None


def clock() -> Clock:
    global _clock
    if _clock is None:
        try:
            from core.paths import get_data_root
            path = Path(get_data_root()) / "clock.json"
        except Exception:
            path = None
        _clock = Clock(path)
    return _clock


# ── инструмент ────────────────────────────────────────────────────────────────

def clock_tool(p: dict) -> str:
    p = p or {}
    a = (p.get("action") or "now").strip().lower()
    c = clock()
    which = str(p.get("label") or p.get("which") or "")
    if a in ("now", "time", "date"):
        return c.now_text()
    if a == "world_time":
        return world_time(str(p.get("city") or ""))
    if a == "timer_set":
        return c.timer_set(parse_duration(p), str(p.get("label") or ""))
    if a == "timer_list":
        return c.timer_list()
    if a == "timer_cancel":
        return c.timer_cancel(which)
    if a == "timer_modify":
        return c.timer_modify(parse_duration(p), which)
    if a == "timer_add":
        return c.timer_add(parse_duration(p), which)
    if a == "timer_pause":
        return c.timer_pause(which)
    if a == "timer_resume":
        return c.timer_resume(which)
    if a == "stopwatch_start":
        return c.sw_start()
    if a == "stopwatch_stop":
        return c.sw_stop()
    if a == "stopwatch_lap":
        return c.sw_lap()
    if a == "stopwatch_reset":
        return c.sw_reset()
    if a == "stopwatch_status":
        return c.sw_status()
    if a == "alarm_set":
        return c.alarm_set(str(p.get("time") or ""), str(p.get("label") or ""), str(p.get("repeat") or "once"))
    if a == "alarm_list":
        return c.alarm_list()
    if a == "alarm_cancel":
        return c.alarm_cancel(str(p.get("time") or which))
    if a == "alarm_stop":
        return c.alarm_stop()
    if a == "alarm_snooze":
        return c.alarm_snooze(float(p.get("minutes") or 5))
    return f"Не понял действие «{a}»."


def world_time(city: str, now: datetime | None = None) -> str:
    """Время в другом городе: пояс — из геокодера (core/location.py)."""
    if not city.strip():
        return "В каком городе, сэр?"
    from core import location
    place = location.geocode(city)
    if not place or not place.get("timezone"):
        return f"Не нашёл город «{city}»."
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(place["timezone"])
    except Exception:
        return f"Не знаю часовой пояс {place['timezone']} — на Windows нужен пакет tzdata."
    there = (now or datetime.now(tz=tz)).astimezone(tz)
    here = (now or datetime.now().astimezone()).astimezone()
    diff = round((there.utcoffset() - here.utcoffset()).total_seconds() / 3600, 1)
    rel = "" if not diff else (f", на {abs(diff):g} {plural(int(abs(diff)), 'час', 'часа', 'часов')} "
                               + ("впереди" if diff > 0 else "позади"))
    return f"В городе {place['name']} сейчас {there:%H:%M}, {WEEKDAYS[there.weekday()]}{rel}."

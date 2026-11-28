"""Джарвис сам заговаривает к месту (TASK-22).

Не «что вам нравится?», а по делу, как в фильме: факт → вывод → предложение,
одной фразой с лёгкой иронией, на которую отвечают «да» или «нет».

Темы первой версии:
  • bedtime  — отбой прошёл, а он смотрит серию / играет. Серию не перебиваем:
               ждём, пока она закончится или встанет на паузу. Предлагает
               будильник на время подъёма из «Обо мне».
  • alarm    — вечер, завтра пары, а будильника до первой пары нет.
  • birthday — сегодня день рождения владельца (дата из «Обо мне»).
«Долго в игре» уже делает core/break_reminder, утро с погодой — core/briefing.

Правила: не чаще раза в час (общий счётчик и с перерывами); тихо во время
звонка, разговора, ответа и подтверждения; «не сейчас / тише» — пауза 3 часа;
дважды отказался от темы — она выключается; каждую тему можно выключить
голосом. Состояние — nudges.json в папке данных: после перезапуска тема
не повторяется.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

TOPICS = ("bedtime", "alarm", "birthday")
TITLES = {"bedtime": "отбой", "alarm": "будильник на пары", "birthday": "день рождения"}
GAP_SEC = 60 * 60            # не чаще раза в час
SNOOZE_SEC = 3 * 60 * 60     # «не сейчас»
REPLY_SEC = 90               # ответ на подсказку — в эти секунды
DISMISS_LIMIT = 2            # дважды «нет» — тема выключается
TICK_SEC = 60
BED_AFTER_MIN = (20, 240)    # отбой: через 20 мин … 4 ч после времени из анкеты
VIDEO_ENDED_SEC = 10 * 60    # серия закончилась недавно — самое время
ALARM_HOURS = (20, 24)       # про будильник — вечером

_SNOOZE_RE = re.compile(r"не\s+сейчас|потом|позже|тише|отстань|не\s+мешай|замолчи", re.I)
_NO_RE = re.compile(r"(?<!\w)(нет|не\s+надо|не\s+нужно|отмена|не\s+хочу|no)(?!\w)", re.I)
_TOPIC_WORDS = {"bedtime": ("отбой", "сон", "спать"), "alarm": ("будильник",),
                "birthday": ("день рождения", "др", "рожден")}


@dataclass
class Seen:
    """Чем занят владелец: kind — video | game | music; playing — идёт ли сейчас."""
    kind: str
    title: str
    playing: bool = True


def _safe(text: str) -> str:
    """Заголовки задают сайты — в указание модели только чистый текст."""
    from core.activity import _safe as clean
    return clean(text)


def _hhmm(text: str) -> tuple[int, int] | None:
    from core.clock import parse_hhmm
    if not (text or "").strip():
        return None
    try:
        return parse_hhmm(text)
    except ValueError:
        return None


def _minutes_after(now: datetime, hm: tuple[int, int]) -> int:
    """Сколько минут прошло после hh:mm (через полночь: отбой 01:00, сейчас 00:30 → 1410)."""
    mark = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    return int(((now - mark).total_seconds() // 60) % (24 * 60))


def _say_minutes(m: int) -> str:
    from core.break_reminder import say_duration
    return say_duration(m)


class Nudges:
    def __init__(self, say: Callable[[str], bool], *, about: Callable[[], dict] = dict,
                 seen: Callable[[], Seen | None] = lambda: None,
                 first_lesson: Callable[[date], str | None] = lambda d: None,
                 alarm_before: Callable[[datetime, datetime], bool] = lambda a, b: False,
                 quiet: Callable[[], str] = lambda: "",
                 now: Callable[[], datetime] = datetime.now, path: Path | None = None):
        self._say, self._about, self._seen = say, about, seen
        self._first_lesson, self._alarm_before, self._quiet = first_lesson, alarm_before, quiet
        self._now, self._path = now, path
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.enabled = {t: True for t in TOPICS}
        self.last: dict[str, str] = {}          # тема → день, когда уже говорил
        self.dismissed: dict[str, int] = {}
        self.snooze_until = 0.0
        self.last_any = 0.0                     # любая инициатива, вкл. перерывы
        self.pending: tuple[str, float] | None = None
        self._video_seen_at = 0.0               # последняя минута, когда видео играло
        self._video_title = ""
        self._load()

    # ── состояние ─────────────────────────────────────────────────────────────
    def _load(self):
        if not self._path or not self._path.is_file():
            return
        try:
            import json
            d = json.loads(self._path.read_text(encoding="utf-8"))
            self.enabled.update({t: bool(v) for t, v in (d.get("enabled") or {}).items() if t in TOPICS})
            self.last = {t: str(v) for t, v in (d.get("last") or {}).items()}
            self.dismissed = {t: int(v) for t, v in (d.get("dismissed") or {}).items()}
            self.snooze_until = float(d.get("snooze_until", 0))
            self.last_any = float(d.get("last_any", 0))
        except Exception as exc:
            logger.warning("Инициатива: настройки не прочитались: %s", exc)

    def _save(self):
        if not self._path:
            return
        try:
            from core.storage import atomic_write_json
            atomic_write_json(self._path, {"enabled": self.enabled, "last": self.last,
                                           "dismissed": self.dismissed, "snooze_until": self.snooze_until,
                                           "last_any": self.last_any})
        except Exception as exc:
            logger.warning("Инициатива: настройки не сохранились: %s", exc)

    def _ts(self) -> float:
        return self._now().timestamp()

    def note_spoken(self):
        """Джарвис только что заговорил сам (и о перерыве тоже) — час тишины."""
        with self._lock:
            self.last_any = self._ts()
            self._save()

    # ── темы ──────────────────────────────────────────────────────────────────
    def _track_video(self, s: Seen | None):
        if s and s.kind == "video" and s.playing:
            self._video_seen_at, self._video_title = self._ts(), s.title

    def _bedtime(self, now: datetime, force: bool = False) -> tuple[str, str] | None:
        about = self._about()
        bed = _hhmm(about.get("sleep_time", ""))
        if not bed:
            return None                         # не знаем отбой — не гадаем
        late = _minutes_after(now, bed)
        if not force and not (BED_AFTER_MIN[0] <= late <= BED_AFTER_MIN[1]):
            return None
        s = self._seen()
        if s and s.kind == "video" and s.playing and not force:
            return None                         # серию не перебиваем — дождёмся конца
        if s and s.kind == "game":
            doing = f"играет в {_safe(s.title)}" if s.title else "играет"
        elif self._video_title and self._ts() - self._video_seen_at <= VIDEO_ENDED_SEC:
            doing = f"только что досмотрел {_safe(self._video_title)}"
        elif force:
            doing = "ещё не спит"
        else:
            return None                         # ничего не делает — может, уже лёг
        night = (now - timedelta(minutes=late)).date().isoformat()
        wake = _hhmm(about.get("wake_time", ""))
        wake_txt = f"{wake[0]:02d}:{wake[1]:02d}" if wake else ""
        offer = (f"Поставить будильник на {wake_txt}?" if wake_txt else "Может, пора закругляться?")
        prompt = ("[СИСТЕМА: инициатива к месту — скажи сам, одной-двумя короткими фразами, "
                  "с лёгкой иронией, по-джарвисовски: факт → вывод → предложение. "
                  f"Сейчас {now:%H:%M}, по анкете владельца отбой в {bed[0]:02d}:{bed[1]:02d} — "
                  f"прошло {_say_minutes(late)}. Он {doing}. "
                  f"Пример: «Сэр, по вашему же расписанию вы спите уже {_say_minutes(late)}. {offer}» "
                  + (f"Ответит «да» — вызови clock с action=\"alarm_set\", time=\"{wake_txt}\". "
                     if wake_txt else "")
                  + "«Нет» — коротко согласись и не настаивай. Не читай лекций о сне.]")
        return night, prompt

    def _alarm(self, now: datetime, force: bool = False) -> tuple[str, str] | None:
        if not force and not (ALARM_HOURS[0] <= now.hour < ALARM_HOURS[1]):
            return None
        tomorrow = now.date() + timedelta(days=1)
        first = _hhmm(self._first_lesson(tomorrow) or "")
        if not first:
            return None
        lesson_at = datetime.combine(tomorrow, datetime.min.time()).replace(hour=first[0], minute=first[1])
        if self._alarm_before(now, lesson_at):
            return None                         # будильник уже стоит
        wake = _hhmm(self._about().get("wake_time", ""))
        wake_at = (datetime.combine(tomorrow, datetime.min.time()).replace(hour=wake[0], minute=wake[1])
                   if wake else None)
        suggest = f"{wake[0]:02d}:{wake[1]:02d}" if wake_at and wake_at < lesson_at else ""
        prompt = ("[СИСТЕМА: инициатива к месту — скажи сам, одной короткой фразой, с лёгкой иронией: "
                  f"завтра первая пара в {first[0]:02d}:{first[1]:02d}, а будильника на утро нет. "
                  + (f"Предложи поставить на {suggest}. Ответит «да» — вызови clock с action=\"alarm_set\", "
                     f"time=\"{suggest}\". " if suggest else
                     "Спроси, на сколько поставить, и поставь через clock action=\"alarm_set\". ")
                  + "«Нет» — коротко согласись.]")
        return now.date().isoformat(), prompt

    def _birthday(self, now: datetime, force: bool = False) -> tuple[str, str] | None:
        from core.study import parse_due
        raw = self._about().get("birthday", "")
        bday = parse_due(raw, now.date()) if raw else None
        if not force and (not bday or bday != now.date() or now.hour < 9):
            return None
        prompt = ("[СИСТЕМА: инициатива — сегодня день рождения владельца. Поздравь сам, коротко, "
                  "тепло и с лёгкой джарвисовской иронией (без стихов и пафоса), одной-двумя фразами. "
                  "Можно предложить включить его любимую музыку.]")
        return now.date().isoformat(), prompt

    _CHECKS = {"bedtime": _bedtime, "alarm": _alarm, "birthday": _birthday}

    # ── такт ──────────────────────────────────────────────────────────────────
    def tick(self) -> str | None:
        """Один замер. Возвращает сказанное указание (для тестов и журнала)."""
        now = self._now()
        ts = now.timestamp()
        self._track_video(self._seen())
        if self.pending and ts - self.pending[1] > REPLY_SEC:
            self.pending = None
        if ts < self.snooze_until or ts - self.last_any < GAP_SEC:
            return None
        why = self._quiet()
        if why:
            return None
        for topic in TOPICS:
            if not self.enabled.get(topic, True):
                continue
            found = self._CHECKS[topic](self, now)
            if not found or self.last.get(topic) == found[0]:
                continue
            return self._fire(topic, found[0], found[1])
        return None

    def _fire(self, topic: str, day: str, prompt: str) -> str | None:
        try:
            spoken = bool(self._say(prompt))
        except Exception as exc:
            logger.warning("Инициатива «%s» не прозвучала: %s", topic, exc)
            spoken = False
        if not spoken:
            return None                         # связи нет — попробуем в следующий раз
        with self._lock:
            self.last[topic] = day
            self.last_any = self._ts()
            self.pending = (topic, self.last_any)
            self._save()
        logger.info("Инициатива: %s", TITLES[topic])
        return prompt

    def on_user_reply(self, text: str) -> None:
        """Ответ на подсказку: «не сейчас» — пауза 3 ч, «нет» — минус теме."""
        p = self.pending
        if not p or self._ts() - p[1] > REPLY_SEC or not (text or "").strip():
            return
        self.pending = None
        topic = p[0]
        with self._lock:
            if _SNOOZE_RE.search(text):
                self.snooze_until = self._ts() + SNOOZE_SEC
                logger.info("Инициатива: пауза 3 ч")
            elif _NO_RE.search(text):
                self.dismissed[topic] = self.dismissed.get(topic, 0) + 1
                if self.dismissed[topic] >= DISMISS_LIMIT:
                    self.enabled[topic] = False
                    logger.info("Инициатива: тема «%s» выключена — дважды отказались", TITLES[topic])
            else:
                self.dismissed[topic] = 0       # согласился — тема полезная
            self._save()

    # ── голосом ───────────────────────────────────────────────────────────────
    def _topic(self, text: str) -> str | None:
        t = (text or "").lower().strip()
        if t in TOPICS:
            return t
        return next((k for k, words in _TOPIC_WORDS.items() if any(w in t for w in words)), None)

    def command(self, action: str, topic: str = "") -> str:
        action = (action or "status").lower()
        key = self._topic(topic)
        if action in ("on", "off"):
            on = action == "on"
            with self._lock:
                for t in ([key] if key else TOPICS):
                    self.enabled[t] = on
                    if on:
                        self.dismissed[t] = 0
                if on and not key:
                    self.snooze_until = 0.0
                self._save()
            what = f"«{TITLES[key]}»" if key else "все подсказки"
            return f"{'Включил' if on else 'Выключил'} {what}, сэр."
        if action == "snooze":
            with self._lock:
                self.snooze_until = self._ts() + SNOOZE_SEC
                self._save()
            return "Хорошо, сэр, три часа молчу."
        if action == "test":
            if not key:
                return "Какую тему проверить: отбой, будильник или день рождения?"
            found = self._CHECKS[key](self, self._now(), force=True)
            if not found:
                return f"Для «{TITLES[key]}» не хватает данных в «Обо мне» или в расписании."
            return found[1]                     # указание — модель его и произнесёт
        on = [TITLES[t] for t in TOPICS if self.enabled.get(t, True)]
        off = [TITLES[t] for t in TOPICS if not self.enabled.get(t, True)]
        quiet = self.snooze_until > self._ts()
        return ("Сам заговариваю про: " + (", ".join(on) or "ничего")
                + (f". Выключено: {', '.join(off)}" if off else "")
                + (". Сейчас на паузе." if quiet else ".") + " Не чаще раза в час.")

    # ── фон ───────────────────────────────────────────────────────────────────
    def start(self):
        def run():
            while not self._stop.wait(TICK_SEC):
                try:
                    self.tick()
                except Exception as exc:
                    logger.debug("Инициатива, такт: %s", exc)
        threading.Thread(target=run, daemon=True, name="nudges").start()
        logger.info("Инициатива к месту: %s", ", ".join(t for t in TOPICS if self.enabled.get(t, True)))

    def stop(self):
        self._stop.set()


_instance: Nudges | None = None


def get(**deps) -> Nudges:
    global _instance
    if _instance is None:
        try:
            from core.paths import get_data_root
            path = Path(get_data_root()) / "nudges.json"
        except Exception:
            path = None
        _instance = Nudges(deps.pop("say", lambda p: False), path=path, **deps)
    return _instance


def nudges_tool(p: dict) -> str:
    p = p or {}
    return get().command(p.get("action", "status"), p.get("topic", ""))

"""«Чёрный ящик» голосового круга: что сказали, что Джарвис услышал, сколько ждали.

Зачем: «не слышит» и «долго отвечает» чинили наугад — по тестам, а не по
живой речи владельца. Здесь каждое событие голосового круга пишется строкой
JSON в blackbox/events-ГГГГ-ММ-ДД.jsonl, а последние секунды микрофона
держатся в памяти. Владелец жмёт F9 («Джарвис ошибся») — эти секунды и
последние события сохраняются в blackbox/miss-ДАТА-ВРЕМЯ.wav/.json, и
разбор идёт по настоящему звуку.

Требования:
  * feed() зовётся из аудио-колбэка — только append в deque, без локов и I/O;
  * ничего не бросать наружу — ящик не имеет права уронить ассистента;
  * звук на диск — только по F9; события — локально, хранятся 14 дней.
"""

from __future__ import annotations

import collections
import json
import logging
import threading
import time
import wave
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

RING_SEC = 20                 # сколько последнего звука держать для F9
KEEP_DAYS = 14                # сколько дней хранить события и записи
_RECENT_EVENTS = 40           # сколько последних событий класть в разбор F9


class BlackBox:
    def __init__(self, root: Path, rate: int = 16000, chunk_bytes: int = 2048):
        self.root = Path(root)
        self.rate = rate
        frames = max(1, int(RING_SEC * rate * 2 / max(chunk_bytes, 1)))
        self._ring: collections.deque[tuple[float, bytes]] = collections.deque(maxlen=frames)
        self._recent: collections.deque[dict] = collections.deque(maxlen=_RECENT_EVENTS)
        self._lock = threading.Lock()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._cleanup()
        except OSError as exc:
            logger.debug("Чёрный ящик: папка %s недоступна: %s", self.root, exc)

    # ── горячий путь ─────────────────────────────────────────────────────────

    def feed(self, pcm: bytes) -> None:
        """Кадр микрофона как есть (до всех шлюзов). Зовётся из аудио-колбэка."""
        self._ring.append((time.time(), pcm))

    # ── события ──────────────────────────────────────────────────────────────

    def event(self, kind: str, **data) -> None:
        rec = {"t": datetime.now().isoformat(timespec="milliseconds"), "kind": kind, **data}
        self._recent.append(rec)
        try:
            line = json.dumps(rec, ensure_ascii=False, default=str)
            path = self.root / f"events-{datetime.now():%Y-%m-%d}.jsonl"
            with self._lock, open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as exc:
            logger.debug("Чёрный ящик: событие не записано: %s", exc)

    # ── F9: «Джарвис ошибся» ─────────────────────────────────────────────────

    def report_miss(self, note: str = "") -> Path | None:
        """Сохранить последние секунды микрофона и события. Возвращает путь к .wav."""
        try:
            frames = list(self._ring)
            stamp = f"{datetime.now():%Y-%m-%d_%H-%M-%S}"
            wav = self.root / f"miss-{stamp}.wav"
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self.rate)
                w.writeframes(b"".join(pcm for _, pcm in frames))
            info = {
                "t": datetime.now().isoformat(timespec="seconds"),
                "note": note,
                "audio_sec": round(sum(len(p) for _, p in frames) / (2 * self.rate), 1),
                "audio_from": datetime.fromtimestamp(frames[0][0]).isoformat(timespec="seconds")
                if frames else None,
                "events": list(self._recent),
            }
            (self.root / f"miss-{stamp}.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            self.event("miss_reported", file=wav.name, audio_sec=info["audio_sec"], note=note)
            logger.info("Чёрный ящик: ошибка отмечена — %s (%.1f с звука)", wav.name, info["audio_sec"])
            return wav
        except Exception as exc:
            logger.warning("Чёрный ящик: не удалось сохранить ошибку: %s", exc)
            return None

    def _cleanup(self) -> None:
        edge = time.time() - KEEP_DAYS * 86400
        for p in self.root.glob("*"):
            try:
                if p.is_file() and p.stat().st_mtime < edge:
                    p.unlink()
            except OSError:
                pass


_box: BlackBox | None = None


def box() -> BlackBox:
    """Общий ящик процесса: %APPDATA%/JARVIS/blackbox."""
    global _box
    if _box is None:
        try:
            from core.paths import get_user_data_dir
            root = get_user_data_dir() / "blackbox"
        except Exception:
            root = Path.home() / ".jarvis_blackbox"
        _box = BlackBox(root)
    return _box

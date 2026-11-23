"""Сколько звука прямо сейчас идёт из динамиков этого компьютера.

Зачем: микрофон слышит комнату целиком, включая музыку из собственных
колонок. В логе это выглядело так — Джарвис прилежно расшифровывал
узбекскую песню и отвечал ей, а живую речь рядом не разбирал.

Отделить одно от другого по громкости нельзя: замерено, музыка даёт на
микрофоне RMS 7000-14000, ровно как речь. Аппаратное шумоподавление ASUS
тоже не помогает — оно снимает вентилятор и клавиатуру, но не музыку
(сравнил оба устройства ввода: медианы 5548 и 5656, разницы нет).

Зато Windows точно знает, что сейчас звучит в колонках, — это и берём.
Пока компьютер играет, микрофон не слушаем.

Замер идёт в отдельном потоке: COM нельзя дёргать из аудио-колбэка, а сам
колбэк обязан возвращаться за миллисекунды.
"""
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

logger = logging.getLogger("jarvis-speaker")

_MAX_SAMPLE_AGE_SEC = 1.5
_POLL_SEC = 0.05          # 50 мс — быстрее, чем длится один кадр микрофона
_DEVICE_CHECK_SEC = 2.0   # как часто сверяться с устройством вывода по умолчанию


class SpeakerMeter:
    """Пиковый уровень вывода в [0..1]. 0.0, если измерить нечем."""

    def __init__(self, poll_sec: float = _POLL_SEC):
        self._peak = 0.0
        self._last_sample_at = None
        self._recent = [0.0, 0.0, 0.0]   # последние замеры: кадр микрофона длиннее одного
        self._poll = poll_sec
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self.available = False

    # ── жизненный цикл ───────────────────────────────────────────────────────
    def start(self, timeout: float = 5.0) -> bool:
        """Измеритель создаёт только рабочий поток — тот, где поднят COM.

        Проба на вызывающем потоке оставляла после себя COM-указатель, который
        сборщик мусора освобождал где попало: «access violation» в __del__."""
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="speaker-meter")
        self._thread.start()
        self._ready.wait(timeout)
        return self.available

    def stop(self):
        self._stop.set()

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def recent(self) -> float:
        """Наибольший уровень за ~150 мс — то, что успело дойти до микрофона за кадр."""
        return max(self._recent)

    # ── внутреннее ───────────────────────────────────────────────────────────
    @staticmethod
    def _default_id() -> str:
        """Идентификатор текущего устройства вывода по умолчанию."""
        from pycaw.pycaw import AudioUtilities
        device = AudioUtilities.GetSpeakers()
        raw = getattr(device, "_dev", None) or device
        try:
            return str(raw.GetId())
        except Exception:
            return ""

    @staticmethod
    def _make_meter():
        from comtypes import CLSCTX_ALL, cast, POINTER
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        device = AudioUtilities.GetSpeakers()
        raw = getattr(device, "_dev", None) or device
        return cast(raw.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None),
                    POINTER(IAudioMeterInformation))

    def _default_id_safe(self) -> str:
        try:
            return self._default_id()
        except Exception:
            return ""

    def _run(self):
        try:
            import comtypes
        except ImportError:                 # не Windows: COM поднимать не нужно
            comtypes = None
        try:
            if comtypes is not None:
                comtypes.CoInitialize()
        except Exception as exc:
            logger.debug("CoInitialize: %s", exc)
        try:
            meter = self._make_meter()
            self.available = True
        except Exception as exc:
            # Не смертельно: без измерителя просто нет этой защиты.
            logger.warning("Уровень динамиков недоступен (%s) — фильтр выключен", exc)
            self.available = False
            return
        finally:
            self._ready.set()
        misses = 0
        current_id = self._default_id_safe()
        checked_at = 0.0
        import time as _time
        while not self._stop.is_set():
            # Пользователь переключил вывод (наушники, HDMI), а старое
            # устройство живо: раньше замер оставался на нём, показывал 0,
            # и музыка из новых колонок снова уходила в Gemini как речь.
            now = _time.monotonic()
            if now - checked_at >= _DEVICE_CHECK_SEC:
                checked_at = now
                new_id = self._default_id_safe()
                if new_id and new_id != current_id:
                    try:
                        meter = self._make_meter()
                        current_id = new_id
                        logger.info("Устройство вывода сменилось — измеритель переключён")
                    except Exception as exc:
                        logger.debug("Переключение измерителя: %s", exc)
            try:
                self._peak = float(meter.GetPeakValue())
                self._last_sample_at = _time.monotonic()
                self._recent = self._recent[1:] + [self._peak]
                misses = 0
            except Exception as exc:
                misses += 1
                if misses == 1:
                    logger.warning("Чтение уровня динамиков сорвалось: %s", exc)
                if misses > 20:
                    # Устройство сменилось — пересоздаём, а не глохнем навсегда.
                    try:
                        meter = self._make_meter()
                        misses = 0
                        logger.info("Измеритель динамиков пересоздан")
                    except Exception:
                        self._peak = 0.0
            self._stop.wait(self._poll)
        try:
            if comtypes is not None:
                comtypes.CoUninitialize()
        except Exception:
            pass

class IsolatedSpeakerMeter:
    """Reads the Windows output meter without loading its COM objects in the UI process."""

    def __init__(self):
        self._samples = ((0.0, 0.0, 0.0), float("-inf"))
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._process = None
        self._stopped = False

    @property
    def available(self) -> bool:
        return not self._stopped and time.monotonic() - self._samples[1] < _MAX_SAMPLE_AGE_SEC

    @property
    def recent(self) -> float:
        values, sampled_at = self._samples
        if self._stopped or time.monotonic() - sampled_at >= _MAX_SAMPLE_AGE_SEC:
            return 0.0
        return max(values)

    def start(self, timeout=8.0):
        args = [sys.executable]
        if not getattr(sys, "frozen", False):
            args.append(str(Path(__file__).resolve()))
        args.append("--speaker-meter-stream")
        with self._lock:
            if self._stopped:
                return False
            try:
                self._process = subprocess.Popen(
                    args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as exc:
                logger.warning("Не удалось запустить измеритель колонок: %s", exc)
                return False
            threading.Thread(target=self._read, args=(self._process,), daemon=True,
                             name="speaker-meter-reader").start()
        self._ready.wait(timeout)
        if not self.available:
            self.stop()
            return False
        return True

    def _read(self, process):
        try:
            with process.stdout as stream:
                for line in stream:
                    try:
                        value = float(line)
                    except ValueError:
                        continue
                    if not math.isfinite(value) or not 0 <= value <= 1:
                        continue
                    values, _ = self._samples
                    self._samples = ((*values[-2:], value), time.monotonic())
                    self._ready.set()
        except OSError as exc:
            logger.debug("Поток измерителя колонок закрыт: %s", exc)
        finally:
            self._samples = ((0.0, 0.0, 0.0), float("-inf"))
            self._ready.set()

    def stop(self):
        with self._lock:
            self._stopped = True
            self._ready.set()
            process = self._process
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass


def stream_worker():
    stream = sys.stdout.buffer if sys.stdout is not None else None
    if stream is None and sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        get_handle = ctypes.windll.kernel32.GetStdHandle
        get_handle.argtypes = (wintypes.DWORD,)
        get_handle.restype = wintypes.HANDLE
        handle = get_handle(-11 & 0xffffffff)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return 1
        stream = os.fdopen(msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY), "wb")
    if stream is None:
        return 1
    meter = SpeakerMeter()
    if not meter.start():
        return 1
    started = time.monotonic()
    try:
        while meter.available:
            sampled_at = meter._last_sample_at
            if time.monotonic() - (sampled_at if sampled_at is not None else started) >= _MAX_SAMPLE_AGE_SEC:
                return 1
            if sampled_at is not None:
                stream.write(f"{meter.peak:.6f}\n".encode("ascii"))
                stream.flush()
            time.sleep(_POLL_SEC)
    except OSError:
        pass
    finally:
        meter.stop()
    return 0


if __name__ == "__main__" and sys.argv[1:] == ["--speaker-meter-stream"]:
    raise SystemExit(stream_worker())

"""Global Windows Hotkey Manager for JARVIS Mark X.

Uses native Win32 user32.RegisterHotKey for zero-latency, reliable system-wide shortcuts:
- F8: Wake JARVIS / Bring to front / Unmute and listen
- Ctrl+Shift+J: Alternative wake shortcut
- Ctrl+Shift+M: Toggle mute globally from any app/game
- F9 / Ctrl+Alt+9: «Джарвис ошибся» — сохранить последние секунды микрофона для разбора
"""

import ctypes
from ctypes import wintypes
import logging
import sys
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

VK_F8 = 0x77
VK_F9 = 0x78
VK_9 = 0x39
VK_J = 0x4A
VK_M = 0x4D

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


class GlobalHotkeyManager:
    """Listens for global Windows shortcuts in a background daemon thread."""

    def __init__(self, on_wake: Optional[Callable] = None, on_mute: Optional[Callable] = None,
                 on_report: Optional[Callable] = None):
        self.on_wake = on_wake
        self.on_mute = on_mute
        self.on_report = on_report
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._thread_id: Optional[int] = None

    def start(self):
        if sys.platform != "win32":
            return
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._message_loop, name="GlobalHotkeys", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass

    def _message_loop(self):
        user32 = ctypes.windll.user32
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        bindings = (
            (1, 0, VK_F8, "F8"),
            (2, MOD_CONTROL | MOD_SHIFT, VK_J, "Ctrl+Shift+J"),
            (3, MOD_CONTROL | MOD_SHIFT, VK_M, "Ctrl+Shift+M"),
            (4, 0, VK_F9, "F9"),
            (5, MOD_CONTROL | MOD_ALT, VK_9, "Ctrl+Alt+9"),
        )
        registered = {}
        for hotkey_id, mods, key, label in bindings:
            if user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, key):
                registered[hotkey_id] = label
            else:
                logger.warning("[Hotkeys] Не удалось зарегистрировать %s", label)
        logger.info("[Hotkeys] Активные сочетания: %s", ", ".join(registered.values()) or "нет")

        msg = wintypes.MSG()
        try:
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                if msg.message == WM_HOTKEY:
                    hotkey_id = msg.wParam
                    if hotkey_id in (1, 2):
                        if self.on_wake:
                            try:
                                self.on_wake()
                            except Exception as exc:
                                logger.warning("Error in on_wake callback: %s", exc)
                    elif hotkey_id == 3:
                        if self.on_mute:
                            try:
                                self.on_mute()
                            except Exception as exc:
                                logger.warning("Error in on_mute callback: %s", exc)
                    elif hotkey_id in (4, 5):
                        if self.on_report:
                            try:
                                self.on_report()
                            except Exception as exc:
                                logger.warning("Error in on_report callback: %s", exc)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)

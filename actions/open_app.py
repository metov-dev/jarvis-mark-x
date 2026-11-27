"""Действие: открыть программу по имени («открой телеграм», «запусти стим»).

Порядок: уже запущена — выводим её окно вперёд (как Siri: не плодим копии);
встроенное в Windows — запускаем сразу; иначе ищем в индексе программ
(меню «Пуск», Microsoft Store, ярлыки, известные пути — core/win_apps.py).
Отвечаем тем, что реально произошло, а не «Открыл» наугад.
"""

import logging
import re
import time
import os
import shutil
import subprocess
import sys

from core import win_apps

_logger = logging.getLogger(__name__)

# Сервисы без программы на ПК — открываем сайт. Лог 26.11: «открой мой инстаграм»
# 45 с пересобирал индекс программ, отвечал «что-то пошло не так», а Gemini за это
# время рвал сессию (1008).
_SITES = {
    "instagram": "https://www.instagram.com", "инстаграм": "https://www.instagram.com",
    "инста": "https://www.instagram.com",
    "youtube": "https://www.youtube.com", "ютуб": "https://www.youtube.com",
    "вк": "https://vk.com", "vk": "https://vk.com", "вконтакте": "https://vk.com",
    "tiktok": "https://www.tiktok.com", "тикток": "https://www.tiktok.com",
    "gmail": "https://mail.google.com", "почта": "https://mail.google.com",
    "github": "https://github.com", "гитхаб": "https://github.com",
    "chatgpt": "https://chatgpt.com", "чатгпт": "https://chatgpt.com",
    "facebook": "https://www.facebook.com", "фейсбук": "https://www.facebook.com",
    "twitter": "https://x.com", "твиттер": "https://x.com",
    "kinogo": "https://kz5.kinogo-films.lol", "киного": "https://kz5.kinogo-films.lol",
}
def _site_for(key: str) -> str:
    """Сайт по имени, даже с хвостом из расшифровки: «instagram говорю», «ютуб пожалуйста»."""
    if key in _SITES:
        return _SITES[key]
    first = key.split()[0] if key.split() else ""
    return _SITES.get(first, "")


_POSSESSIVE = re.compile(r"^(?:мой|моя|моё|мое|мои|мою)\s+", re.IGNORECASE)
_FORCE_REBUILD_SEC = 600.0
_last_forced_rebuild = float("-inf")


def open_app(parameters: dict, response=None, player=None) -> str:
    raw = (parameters.get("app_name") or "").strip()
    if not raw:
        return "Не указано имя приложения."
    if any(c in raw for c in ("\\", "/")) or (len(raw) > 2 and raw[1] == ":"):
        # Путь к файлу — не приложение. Раньше «открой c:\...\x.bat» или
        # \\сервер\share\x.exe (из Telegram или от модели) запускались как есть.
        return f"Запускаю только приложения по имени, а не файлы по пути: «{raw}»."

    raw = _POSSESSIVE.sub("", raw).strip() or raw
    key = win_apps.canonical(raw)

    if key in ("браузер", "browser"):
        import webbrowser
        webbrowser.open("https://www.google.com")
        return "Открыл браузер."

    if sys.platform != "win32":
        return _open_posix(key, raw)

    # 1. Уже запущена — просто показать.
    running = win_apps.find_windows(key)
    if running and key not in ("explorer",):
        win = running[0]
        if win_apps.focus(win):
            return f"{_title(raw)} уже открыт — переключил на него."

    # 2. Встроенное в Windows.
    for cmd in win_apps.BUILTIN.get(key, []):
        try:
            os.startfile(cmd)  # type: ignore[attr-defined]
            return f"Открыл {_title(raw)}."
        except OSError:
            continue

    # 3. Индекс программ.
    app = win_apps.find_app(key)
    site = _site_for(key)
    if app is None and site:
        import webbrowser
        webbrowser.open(site)
        return f"Открыл {_title(key.split()[0])} в браузере."
    global _last_forced_rebuild
    if app is None and time.monotonic() - _last_forced_rebuild >= _FORCE_REBUILD_SEC:
        # Поставили только что? Пересборка индекса — 30–45 с (журнал 27.11 23:50:
        # ответ ждал её 45 с). Теперь в фоне: этот ответ — сразу, следующий найдёт.
        _last_forced_rebuild = time.monotonic()
        import threading
        threading.Thread(target=win_apps.build_index, kwargs={"force": True},
                         daemon=True, name="apps-reindex").start()
    if app:
        try:
            win_apps.launch(app)
            if player:
                player.write_log(f"SYS: запускаю {app.name}")
            return f"Открыл {app.name}."
        except Exception as exc:
            _logger.warning("Запуск %s: %s", app, exc)
            return f"Нашёл {app.name}, но не смог запустить: {exc}"

    # 4. Последний шанс: имя exe в PATH / App Paths.
    for cmd in (f"{key}.exe", key):
        path = shutil.which(cmd)
        if path:
            os.startfile(path)  # type: ignore[attr-defined]
            return f"Открыл {_title(raw)}."

    near = win_apps.suggestions(raw)
    hint = f" Похожие: {', '.join(near)}." if near else ""
    return f"Не нашёл программу «{raw}» на компьютере.{hint}"


def _title(raw: str) -> str:
    return raw[:1].upper() + raw[1:]


def _open_posix(key: str, raw: str) -> str:
    for cmd in (key, raw.lower()):
        path = shutil.which(cmd)
        if path:
            subprocess.Popen([path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return f"Открыл {_title(raw)}."
    return f"Не нашёл программу «{raw}»."

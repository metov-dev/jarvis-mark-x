"""Прогон функций ядра без голоса: каждая вызывается по-настоящему, результат
проверяется, где это можно проверить. Итог — таблица «работает / нет / мс».

    .venv/Scripts/python.exe scripts/core_smoke.py

Ничего не ломает: таймер ставится и сразу снимается, Блокнот открывается и
закрывается, музыка не включается (только «что играет»).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

ROWS: list[tuple[str, str, int, str]] = []
FAIL_WORDS = ("не удалось", "не получилось", "не нашёл", "не нашел", "ошибк", "не могу", "недоступ",
              "нет связи", "не подключ", "не понял")


def check(name: str, fn) -> None:
    t = time.perf_counter()
    try:
        ok, note = fn()
    except Exception as exc:                       # прогон не падает из-за одной функции
        ok, note = False, f"{type(exc).__name__}: {exc}"
    ROWS.append((name, "OK" if ok else "FAIL", int((time.perf_counter() - t) * 1000), str(note)[:90]))


def said_ok(text: str) -> tuple[bool, str]:
    low = (text or "").lower()
    return bool(text) and not any(w in low for w in FAIL_WORDS), text


def t_time():
    from core.clock import clock_tool
    return said_ok(clock_tool({"action": "now"}))


def t_timer():
    from core.clock import clock_tool
    set_ = clock_tool({"action": "timer_set", "minutes": 59, "label": "проверка"})
    listed = clock_tool({"action": "timer_list"})
    clock_tool({"action": "timer_cancel", "label": "проверка"})
    ok = "проверка" in listed.lower() or "59" in listed
    return ok, f"{set_} | список: {listed}"


def t_alarm_list():
    from core.clock import clock_tool
    return said_ok(clock_tool({"action": "alarm_list"}))


def t_weather():
    from actions.weather import weather_action
    return said_ok(weather_action(parameters={}, player=None))


def _notepad_pids() -> set[str]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq notepad.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return {line.split('","')[1] for line in out.splitlines() if line.startswith('"notepad')}


def t_open_app():
    from actions.open_app import open_app
    before = _notepad_pids()
    text = open_app(parameters={"app_name": "блокнот"})
    for _ in range(20):                            # проверяем по-настоящему: процесс появился
        new = _notepad_pids() - before
        if new:
            break
        time.sleep(0.25)
    for pid in new:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
    return bool(new), f"{text} | новый процесс: {'да' if new else 'НЕТ'}"


def t_find_apps():
    from core import win_apps
    hits = {n: bool(win_apps.find_app(n)) for n in ("telegram", "spotify", "chrome", "яндекс браузер")}
    return any(hits.values()), ", ".join(f"{k}:{'да' if v else 'нет'}" for k, v in hits.items())


def t_music_status():
    from actions.music_player import music_player
    return said_ok(music_player({"action": "now_playing"}))


def t_bot():
    url = "https://atabekovch-jarvis-mark-x.hf.space/health"
    with urllib.request.urlopen(url, timeout=20) as r:
        body = r.read(200).decode("utf-8", "replace")
        return r.status == 200, f"{r.status} {body}"


def t_gemini_live():
    import main as M
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=M._get_api_key(), http_options={"api_version": "v1beta"})
    cfg = types.LiveConnectConfig(response_modalities=["AUDIO"])

    async def go():
        for model in M.LIVE_MODELS:
            try:
                async with client.aio.live.connect(model=model, config=cfg) as s:
                    await s.send_client_content(turns=types.Content(
                        role="user", parts=[types.Part(text="Скажи: да")]), turn_complete=True)
                    async for r in s.receive():
                        if r.data:
                            return True, f"{model.split('/')[-1]} отвечает звуком"
            except Exception as exc:
                last = f"{model.split('/')[-1]}: {exc}"
        return False, last
    return asyncio.run(asyncio.wait_for(go(), 40))


def t_memory():
    from memory import conversation
    data = conversation._gemini_json('Верни JSON {"ok": true}')
    return data.get("ok") is True, str(data)


def t_fish():
    from telegram_bot import tts_fish
    if not tts_fish.is_configured():
        return False, "Fish не настроен"
    pcm = asyncio.run(tts_fish.speak_pcm("Да, сэр.", 24000))
    return bool(pcm) and len(pcm) > 4000, f"{len(pcm or b'')} байт звука"


def t_edge():
    from telegram_bot import tts_edge
    pcm = asyncio.run(tts_edge.speak_pcm("Да, сэр.", sample_rate=24000))
    return bool(pcm) and len(pcm) > 4000, f"{len(pcm or b'')} байт звука"


def main() -> int:
    for name, fn in [
        ("Время", t_time), ("Таймер: поставить/список/снять", t_timer), ("Будильники: список", t_alarm_list),
        ("Погода", t_weather), ("Открыть приложение (Блокнот)", t_open_app), ("Поиск программ", t_find_apps),
        ("Музыка: что играет", t_music_status), ("Telegram-бот /health", t_bot),
        ("Gemini Live", t_gemini_live), ("Память (лестница моделей)", t_memory),
        ("Голос Fish", t_fish), ("Голос Edge (запасной)", t_edge),
    ]:
        check(name, fn)
    w = max(len(r[0]) for r in ROWS)
    for name, st, ms, note in ROWS:
        print(f"{st:4} {name:{w}} {ms:>6} мс  {note}")
    bad = sum(r[1] != "OK" for r in ROWS)
    print(f"\nИтог: {len(ROWS) - bad} из {len(ROWS)} работают")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

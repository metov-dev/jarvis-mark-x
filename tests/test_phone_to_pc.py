"""Команды с телефона доходят до Джарвиса на ПК, а не теряются (28.11).

Скриншоты: «громкость на сто» и «какая нахуй музыка» → «Продолжаю музыку»;
«скажи вслух: привет, Сарварчик» → ИИ бота писал «Передаю команду на ПК»,
ничего не передавая; «закрой браузер» → «Окно «browser» не найдено».
"""
import asyncio
from types import SimpleNamespace

from telegram_bot import pc_directive, pc_server


def test_volume_number_is_volume_not_music():
    assert pc_server._volume_level("да так точно но сначало громкость на сто сделай") == 100
    assert pc_server._volume_level("сделай громкость 40") == 40
    assert pc_server._volume_level("громкость голоса у неё приятная") is None


def test_music_word_without_verb_is_not_play():
    assert pc_server._parse_music("далбаеб чтоли какая нахуй музыка") is None
    assert pc_server._parse_music("включи музыку") == {"action": "play", "query": ""}
    assert pc_server._parse_music("выключи музыку") == {"action": "pause"}


def test_insult_with_music_word_is_unknown(monkeypatch):
    res = asyncio.run(pc_server._execute("Далбаеб чтоли какая нахуй музыка"))
    assert res.get("unknown"), res


class _Bridge:
    connected = True

    def __init__(self, answer):
        self.answer, self.sent = answer, []

    async def send_action(self, action, user_id, timeout=25.0, **fields):
        self.sent.append((action, fields))
        return self.answer

    async def send_command_full(self, text, user_id, timeout=25.0):
        return {"text": "старый путь"}


def test_pc_block_goes_to_jarvis_with_real_result():
    br = _Bridge({"ok": True, "text": "Привет, Сарварчик."})
    reply = "Сейчас, сэр.\n[[PC]]\nскажи вслух: Привет, Сарварчик\n[[/PC]]"
    out = asyncio.run(pc_directive.apply(br, reply, 1))
    assert br.sent == [("agent", {"request": "скажи вслух: Привет, Сарварчик"})]
    assert "[[PC]]" not in out and "🖥 Привет, Сарварчик." in out


def test_no_block_means_no_false_claim():
    out = asyncio.run(pc_directive.apply(_Bridge(None), "Ах, понял.\n\nПередаю команду на ПК. Привет, Сарварчик.", 1))
    assert "команду на пк" not in out.lower()


def test_unsafe_and_untrusted_blocks_do_nothing():
    br = _Bridge({"ok": True, "text": "x"})
    asyncio.run(pc_directive.apply(br, "[[PC]]выключи компьютер[[/PC]]", 1))
    asyncio.run(pc_directive.apply(br, "[[PC]]отправь маме привет[[/PC]]", 1))
    asyncio.run(pc_directive.apply(br, "[[PC]]включи музыку[[/PC]]", 1, trusted=False))
    assert br.sent == []
    assert pc_directive.is_safe("включи музыку")        # «ключ» внутри «включи» — не ключ


def test_offline_pc_says_so():
    br = _Bridge(None)
    br.connected = False
    out = asyncio.run(pc_directive.apply(br, "[[PC]]выключи музыку[[/PC]]", 1))
    assert "не в сети" in out


def test_agent_action_uses_running_jarvis(monkeypatch):
    seen = []
    monkeypatch.setattr(pc_server, "_agent", lambda t: seen.append(t) or {"ok": True, "text": "Громкость 100%."})
    res = asyncio.run(pc_server._run_agent("поставь громкость на 100"))
    assert seen == ["поставь громкость на 100"] and res == {"ok": True, "text": "Громкость 100%."}


def test_close_browser_finds_yandex_windows():
    from core import win_apps
    W = lambda exe, title: SimpleNamespace(exe=exe, title=title, hwnd=1)
    wins = [W("browser.exe", "(190) Why Samurai Feared — Яндекс Браузер"), W("msedge.exe", "Завод"),
            W("browser.exe", "Киного")]
    assert [w.title for w in win_apps.find_windows("browser", wins)] == [wins[0].title, "Киного"]
    assert len(win_apps.find_windows("браузер", wins)) == 2


def test_close_closes_every_window_of_the_app(monkeypatch):
    from actions import window_control as wc
    from core import win_apps
    W = lambda title: SimpleNamespace(exe="browser.exe", title=title, hwnd=1)
    wins = [W("a"), W("b")]
    closed = []
    monkeypatch.setattr(win_apps, "find_windows", lambda t: wins)
    monkeypatch.setattr(win_apps, "close", lambda w: closed.append(w.title) or True)
    out = wc._window_op("close", "browser")
    assert closed == ["a", "b"] and "все окна" in out


def test_close_video_works_while_movie_loads(monkeypatch):
    from actions import video_player as vp
    tab = SimpleNamespace(navigated=[], navigate=lambda u: tab.navigated.append(u))
    monkeypatch.setattr(vp.cdp, "tab", lambda create=True: tab)
    monkeypatch.setattr(vp.cdp, "fullscreen", lambda *a, **k: True)
    monkeypatch.setattr(vp.cdp, "video_js", lambda *a, **k: None)
    monkeypatch.setattr(vp, "_save_current_watch", lambda: None)
    with vp._movie_launch_lock:
        assert vp.control("close") == "Закрыл видео."
    assert tab.navigated == ["about:blank"]


def test_remote_command_returns_what_jarvis_did():
    import threading
    import main
    j = main.Jarvis.__new__(main.Jarvis)
    j._remote, j._remote_lock = None, threading.Lock()

    def typed(text):
        j._remote_tool("computer_settings", {"result": "Громкость 100%."})
        j._remote_reply("Громкость на максимуме, сэр.")
    j._on_text_command = typed
    res = j.remote_command("поставь громкость на 100", timeout=2)
    assert res == {"ok": True, "text": "Громкость на максимуме, сэр."}
    assert j._remote is None

    j._on_text_command = lambda text: None          # Джарвис молчит
    res = j.remote_command("что-то", timeout=0.2)
    assert res["ok"] is False and "не ответил" in res["text"]

"""Фильмы (VK), ролики (YouTube) и голосовое управление просмотром.

Модульные тесты — на подменённом плеере. Интеграционный — на настоящем
Chrome/Chromium с локальной страницей и видео (пропускается, если браузера
нет): запуск, полный экран, перемотка, громкость, mute, пауза, время.
"""
import http.server
import json
import os
import re
import shutil
import subprocess
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from actions import video_player as vp
from core import browser_cdp as cdp


# ── разбор времени ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,sec", [
    ("1:20:00", 4800), ("20:30", 1230), ("5 минут", 300), ("10 секунд", 10),
    ("полчаса", 1800), ("1 час 20 минут", 4800), ("две минуты", 120), ("30", 30),
])
def test_parse_seconds(text, sec):
    assert vp.parse_seconds(text) == sec


# ── выбор фильма из выдачи VK ────────────────────────────────────────────────

RESULTS = [
    {"id": "-1_1", "title": "Железный человек 2 — официальный трейлер", "dur": "2:31"},
    {"id": "-1_2", "title": "Железный человек 2 (2010) фильм", "dur": "2:04:47"},
    {"id": "-1_3", "title": "Обзор фильма Железный человек 2", "dur": "18:02"},
    {"id": "-1_4", "title": "Мстители", "dur": "2:23:00"},
]


def test_film_prefers_full_movie_over_trailer_and_review():
    assert vp._pick_vk(RESULTS, "Железный человек 2", film=True)["id"] == "-1_2"


def test_non_film_search_takes_best_title_match():
    assert vp._pick_vk(RESULTS[:1], "Железный человек 2", film=False)["id"] == "-1_1"


def test_kinogo_selects_exact_title_and_year_on_requested_host():
    movie = vp._KINOGO_HOME + "123-dyuna.html"
    another = vp._KINOGO_HOME + "456-dyuna.html"
    links = [{"url": another, "title": "Дюна (1984)"},
             {"url": movie, "title": "Дюна (2021) смотреть онлайн"},
             {"url": "https://elsewhere.example/dyuna.html", "title": "Дюна (2021)"}]
    assert vp._kinogo_matches("Дюна 2021", links) == {movie}
    assert vp._kinogo_matches("Дюна", links) == {movie, another}


def test_movie_command_defaults_to_kinogo_and_accepts_explicit_vk(monkeypatch):
    from actions.movie_player import movie_player

    monkeypatch.setattr(vp, "play_kinogo", lambda title, player, launch: f"Kinogo {title}")
    monkeypatch.setattr(vp, "play_film", lambda title, player, launch: f"VK {title}")
    assert movie_player({"action": "play", "title": "Дюна"}) == "Kinogo Дюна"
    assert movie_player({"action": "play", "title": "Дюна", "provider": "vk"}) == "VK Дюна"


def test_kinogo_reports_only_confirmed_playback_and_fullscreen(monkeypatch):
    url = vp._KINOGO_HOME + "123-dyuna.html"
    monkeypatch.setattr(cdp, "ensure_browser", lambda **_: True)
    status = {"paused": True, "fs": True}

    def start(actual_url, fullscreen, embedded, launch, episode=None):
        assert actual_url == url and fullscreen and embedded and episode is None
        return status

    monkeypatch.setattr(vp, "_start", start)
    assert "пока не началось" in vp.play_kinogo(url)
    status.update(paused=False, fs=False)
    assert "Полный экран включить не удалось" in vp.play_kinogo(url)
    status["fs"] = True
    assert "во весь экран" in vp.play_kinogo(url)


@pytest.mark.parametrize(("spoken_title", "expected_queries"), [
    ("Бэтмен начало 2005", ["бэтмен начало", "бэтмен: начало"]),
    ("Бэтмен. Э, начало", ["бэтмен. э, начало", "бэтмен начало", "бэтмен: начало"]),
    ("Бэтмен. Э, начало 2005", ["бэтмен. э, начало", "бэтмен начало", "бэтмен: начало"]),
])
def test_kinogo_search_uses_title_and_checks_year_on_result(monkeypatch, spoken_title, expected_queries):
    import json
    url = vp._KINOGO_HOME + "123-batman-begins.html"
    visited = []
    form_queries = []

    class SearchTab:
        def navigate(self, target):
            visited.append(target)

        def wait(self, js, timeout):
            return True

        def eval(self, js, timeout=10):
            if js == vp._KINOGO_ACCESS_DENIED:
                return False
            if js == vp._KINOGO_LINKS:
                return ([{"url": url, "title": "Бэтмен: Начало (2005)"}]
                        if form_queries[-1] == "бэтмен: начало" else [])
            if js.startswith(vp._KINOGO_SEARCH):
                form_queries.append(json.loads(js[len(vp._KINOGO_SEARCH) + 1:-1]))
            return True

    monkeypatch.setattr(cdp, "ensure_browser", lambda **_: True)
    monkeypatch.setattr(cdp, "tab", SearchTab)
    monkeypatch.setattr(vp, "_start", lambda target, **_: visited.append(target) or {"paused": False, "fs": True})
    assert "во весь экран" in vp.play_kinogo(spoken_title)
    assert form_queries == expected_queries
    assert visited == [vp._KINOGO_HOME, url]


def test_kinogo_hesitation_fallback_preserves_real_title_words():
    assert list(vp._kinogo_search_queries("Люди в чёрном")) == [
        ("люди в чёрном", "Люди в чёрном"),
    ]
    assert list(vp._kinogo_search_queries("Бэтмен. Ну, начало")) == [
        ("бэтмен. ну, начало", "Бэтмен. Ну, начало"),
    ]


def test_kinogo_title_year_matches_metadata_and_rejects_wrong_year():
    movie = vp._KINOGO_HOME + "123-batman-begins.html"
    wrong = vp._KINOGO_HOME + "456-batman.html"
    rows = [
        {"url": wrong, "title": "Бэтмен: Начало", "year": "2022"},
        {"url": movie + "#watch", "title": "Смотреть", "alternate": "Бэтмен: Начало (2005) смотреть онлайн"},
        {"url": movie, "title": "Бэтмен: Начало / Batman Begins", "year": "2005"},
    ]
    assert vp._kinogo_matches("Бэтмен начало 2005", rows) == {movie}
    assert vp._kinogo_matches("Бэтмен начало 2010", rows) == set()
    assert vp._kinogo_matches("Бэтмен начало", rows) == {movie, wrong}
    assert vp._film_title_year("Бегущий по лезвию 2049") == ("бегущий по лезвию 2049", None)


def test_kinogo_tries_alternate_when_default_player_has_no_video(monkeypatch):
    selected = []
    clock = [0.0]
    state = {"paused": False, "fs": True}
    url = vp._KINOGO_HOME + "123-batman-begins.html"

    class Tab:
        def navigate(self, target):
            assert target == url

        def enable_embedded_video(self):
            pass

        def eval(self, script, timeout):
            assert script == vp._KINOGO_ALTERNATE_PLAYER
            selected.append(script)
            return True

    monkeypatch.setattr(vp.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(vp.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(cdp, "tab", Tab)
    monkeypatch.setattr(cdp, "pin_film_player", lambda *a, **kw: bool(selected))
    monkeypatch.setattr(cdp, "video_js", lambda *a, **kw: state)
    monkeypatch.setattr(cdp, "video_state", lambda *a: state)
    monkeypatch.setattr(cdp, "bring_to_front", lambda: None)
    monkeypatch.setattr(cdp, "fullscreen", lambda *a: True)
    assert vp._start(url, embedded=True) == state
    assert selected == [vp._KINOGO_ALTERNATE_PLAYER]


def test_expired_movie_request_cannot_start_player(monkeypatch):
    url = vp._KINOGO_HOME + "film.html"
    launch = vp.MovieLaunch()

    class Tab:
        def navigate(self, target):
            assert target == url

        def wait(self, script, timeout):
            launch.cancelled.set()
            return True

    monkeypatch.setattr(cdp, "tab", lambda: Tab())
    monkeypatch.setattr(cdp, "video_js", lambda *a, **k: pytest.fail("Playback started after cancellation"))
    with pytest.raises(TimeoutError):
        vp._start(url, launch=launch)


def test_embedded_video_commands_use_the_iframe_session(monkeypatch):
    calls = []
    messages = deque()
    state = {"paused": False, "ready": 4, "t": 30}

    class BrowserSocket:
        def send(self, data):
            req = json.loads(data)
            calls.append(req)
            method = req["method"]
            result = {}
            if method == "Target.setAutoAttach":
                messages.append({"method": "Target.attachedToTarget", "params": {
                    "targetInfo": {"targetId": "frame", "type": "iframe"}, "sessionId": "child",
                }})
            if method == "Page.createIsolatedWorld":
                assert req["sessionId"] == "child" and req["params"]["frameId"] == "frame"
                result = {"executionContextId": 7}
            if method == "Runtime.evaluate" and req.get("sessionId") == "child":
                assert req["params"]["contextId"] == 7
                if "v.pause()" in req["params"]["expression"]:
                    state["paused"] = True
                result = {"result": {"value": dict(state)}}
            messages.append({"id": req["id"], "result": result})

        def recv(self, timeout):
            return json.dumps(messages.popleft())

    monkeypatch.setattr("websockets.sync.client.connect", lambda *a, **kw: BrowserSocket())
    tab = cdp.Tab("ws://localhost", "top")
    tab.enable_embedded_video()
    assert cdp.video_state(tab)["t"] == 30
    assert cdp.video_js("v.pause()", tab)["paused"]
    assert calls[-1]["sessionId"] == "child"


# ── управление на подменённом плеере ─────────────────────────────────────────

@pytest.fixture
def fake_player(monkeypatch):
    st = {"paused": False, "t": 600.0, "d": 7200.0, "vol": 0.5, "muted": False, "fs": True, "ready": 4}

    def video_js(body, t=None, await_promise=False):
        if "v.pause()" in body and "paused ?" not in body:
            st["paused"] = True
        if "v.play()" in body:
            st["paused"] = False
        m = re.search(r"v\.currentTime \+ (-?\d+(?:\.\d+)?)", body)
        if m:
            st["t"] = max(0, min(st["d"], st["t"] + float(m.group(1))))
        m = re.search(r"Math\.min\(v\.duration \|\| 1e9, (\d+(?:\.\d+)?)\)", body)
        if m:
            st["t"] = float(m.group(1))
        m = re.search(r"v\.volume = (\d+(?:\.\d+)?)", body)
        if m:
            st["vol"] = float(m.group(1))
        if "v.muted = true" in body:
            st["muted"] = True
        return dict(st)

    monkeypatch.setattr(cdp, "video_state", lambda t=None: dict(st))
    monkeypatch.setattr(cdp, "video_js", video_js)
    monkeypatch.setattr(cdp, "fullscreen", lambda on=True, t=None: st.update(fs=on) or True)
    return st


def test_controls_do_what_they_say(fake_player):
    assert vp.control("pause") == "Пауза." and fake_player["paused"]
    assert vp.control("resume") == "Продолжаю."
    assert vp.control("seek_forward", "5 минут") == "Вперёд на 5 мин — 15:00."
    assert vp.control("seek_back", None) == "Назад на 10 с — 14:50."
    assert vp.control("seek_to", "1:20:00") == "Перемотал на 1:20:00."
    assert vp.control("time") == "Идёт 1:20:00 из 2:00:00, осталось 40 мин."
    assert vp.control("volume_set", "30") == "Громкость видео 30%."
    assert vp.control("volume_up") == "Громкость видео 45%."
    assert vp.control("mute") == "Звук видео выключен." and fake_player["muted"]
    assert vp.control("exit_fullscreen") == "Вышел из полного экрана." and not fake_player["fs"]


def test_no_video_returns_empty_for_caller(monkeypatch):
    monkeypatch.setattr(cdp, "video_state", lambda t=None: None)
    assert vp.control("pause") == ""


def test_film_loading_does_not_control_previous_video(monkeypatch):
    monkeypatch.setattr(cdp, "video_state", lambda: pytest.fail("Read previous video during launch"))
    with vp._movie_launch_lock:
        assert vp.control("pause") == "Плеер фильма пока недоступен."


@pytest.mark.asyncio
@pytest.mark.parametrize("target, loading", [("video", False), ("auto", True)])
async def test_film_pause_cannot_pause_spotify(monkeypatch, target, loading):
    import main

    jarvis = main.Jarvis.__new__(main.Jarvis)
    jarvis.ui = SimpleNamespace(set_state=lambda _: None, muted=True)
    monkeypatch.setattr(vp, "control", lambda *args: "")
    monkeypatch.setattr(vp, "film_target_active", lambda: loading)
    monkeypatch.setattr(main, "spotify_player", lambda **kw: pytest.fail("Spotify was called"))
    call = SimpleNamespace(id="test", name="video_control", args={"action": "pause", "target": target})
    result = await jarvis._execute_tool(call)
    assert result.response["result"] == "Плеер фильма пока недоступен."


def test_yandex_default_uses_its_own_browser_port_and_profile(monkeypatch, tmp_path):
    from core import paths

    executable = str(tmp_path / "browser.exe")
    monkeypatch.delenv("JARVIS_BROWSER", raising=False)
    monkeypatch.delenv("JARVIS_CDP_PORT", raising=False)
    monkeypatch.setattr(cdp.sys, "platform", "win32")
    monkeypatch.setattr("actions.browser_control._browser_exe",
                        lambda name: executable if name == "yandex" else None)
    monkeypatch.setattr(cdp, "_windows_default_browser", lambda: str(tmp_path / "msedge.exe"))
    monkeypatch.setattr(cdp, "PORT", 9229)
    monkeypatch.setattr(paths, "get_data_root", lambda: tmp_path)

    assert cdp.browser_exe() == executable
    assert cdp._browser_port() == 9230
    assert os.path.basename(cdp._profile_dir()) == "browser-yandex"

    explicit = tmp_path / "msedge.exe"
    explicit.touch()
    monkeypatch.setenv("JARVIS_BROWSER", str(explicit))
    assert cdp.browser_exe() == str(explicit)
    assert cdp._browser_port() == 9229
    assert os.path.basename(cdp._profile_dir()) == "browser"


def test_music_pause_goes_to_film_when_only_film_plays(monkeypatch, fake_player):
    from actions import music_player as mp
    monkeypatch.setattr(vp, "video_playing", lambda: True)
    monkeypatch.setattr(mp, "_music_playing", lambda: False)
    assert mp.music_player({"action": "pause"}) == "Пауза." and fake_player["paused"]


# ── настоящий браузер ────────────────────────────────────────────────────────

def _chrome():
    for c in (os.getenv("JARVIS_BROWSER"), shutil.which("google-chrome"), shutil.which("chromium"),
              "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"):
        if c and os.path.isfile(c):
            return c
    return None


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


class _Range(http.server.SimpleHTTPRequestHandler):
    """Отдаёт файлы кусками (Range) — как настоящие видеосайты: без этого
    перемотка в браузере сбрасывается на начало."""

    def send_head(self):
        path = self.translate_path(self.path)
        rng = self.headers.get("Range")
        if not rng or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        a, b = re.match(r"bytes=(\d*)-(\d*)", rng).groups()
        a, b = int(a or 0), (int(b) if b else size - 1)
        f = open(path, "rb")
        f.seek(a)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.send_header("Content-Length", str(b - a + 1))
        self.end_headers()
        self._left = b - a + 1
        return f

    def copyfile(self, src, dst):
        left = getattr(self, "_left", None)
        if left is None:
            return super().copyfile(src, dst)
        while left > 0:
            chunk = src.read(min(65536, left))
            if not chunk:
                break
            dst.write(chunk)
            left -= len(chunk)

    def log_message(self, *a):
        pass


@pytest.mark.skipif(not (_chrome() and _ffmpeg()), reason="нет Chrome/Chromium или ffmpeg")
@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.parametrize("start_at", [0, 32])
def test_real_browser_film_flow(tmp_path, monkeypatch, embedded, start_at):
    monkeypatch.setattr(vp, "_FILM_MIN_SEC", 60)
    subprocess.run([_ffmpeg(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=90:size=160x120:rate=5", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=90", "-c:v", "libvpx", "-b:v", "60k",
                    "-c:a", "libopus", "-shortest", str(tmp_path / "movie.webm")], check=True)
    (tmp_path / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Фильм — VK Видео</title>'
        '<div class="videoplayer"><video src="movie.webm" style="width:640px;height:360px"></video></div>'
        '<video src="movie.webm" style="width:60px;height:40px"></video>', encoding="utf-8")
    if embedded:
        subprocess.run([_ffmpeg(), "-loglevel", "error", "-y", "-i", str(tmp_path / "movie.webm"),
                        "-t", "3", "-c", "copy", str(tmp_path / "preview.webm")], check=True)
    handler = lambda *a, **k: _Range(*a, directory=str(tmp_path), **k)  # noqa: E731
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    (tmp_path / "embed.html").write_text(
        f'<iframe allowfullscreen src="http://localhost:{srv.server_address[1]}/index.html" '
        'style="width:680px;height:400px"></iframe>'
        '<video src="preview.webm" style="width:1400px;height:800px"></video>', encoding="utf-8")
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    monkeypatch.setattr(cdp, "PORT", 9400 + os.getpid() % 500)
    monkeypatch.setattr(cdp, "_tab", None)
    monkeypatch.setattr(cdp, "_profile_dir", lambda: str(tmp_path / "profile"))
    monkeypatch.setenv("JARVIS_BROWSER", _chrome())
    args = "--headless=new --mute-audio --disable-gpu --in-process-gpu" + (
        " --no-sandbox" if hasattr(os, "geteuid") and os.geteuid() == 0 else "")
    monkeypatch.setenv("JARVIS_BROWSER_ARGS", args)
    try:
        assert cdp.ensure_browser(), cdp.browser_log()[-2000:]
        page = "embed.html" if embedded else "index.html"
        st = vp._start(f"http://127.0.0.1:{srv.server_address[1]}/{page}",
                       embedded=embedded, position=start_at)
        assert st and not st["paused"] and st["fs"], st
        assert start_at - 1 <= st["t"] < start_at + 3
        assert vp.control("seek_to", "0:45") == "Перемотал на 0:45."
        assert vp.control("seek_forward", "15 секунд").endswith("1:00.")
        assert vp.control("volume_set", "40") == "Громкость видео 40%."
        assert vp.control("mute") == "Звук видео выключен."
        assert vp.control("pause") == "Пауза."
        assert vp.control("time").startswith("Идёт 1:00 из 1:30")
        assert vp.control("exit_fullscreen") == "Вышел из полного экрана."
        assert vp.video_playing() is False
        if embedded:
            cdp.tab()._film_player.eval("window.__jarvisFilm.remove()")
            assert vp.control("resume") == ""
    finally:
        try:
            if cdp._proc:
                cdp._proc.terminate()
                cdp._proc.wait(timeout=5)
        finally:
            srv.shutdown()


# ─── Провал не должен повторяться по кругу ───────────────────────────────────
def _film_that_never_starts(monkeypatch):
    """VK открывается, но плеер не поднимается — живой случай без входа в VK."""
    calls = []
    monkeypatch.setattr(cdp, "ensure_browser", lambda: True)
    monkeypatch.setattr(vp, "vk_find", lambda t, film=True: "https://vkvideo.ru/x")
    monkeypatch.setattr(vp, "_start", lambda url, fullscreen=True, wait_sec=15.0:
                        calls.append(url) or None)
    vp._last_fail = None
    return calls


def test_failed_film_says_plainly_that_retrying_will_not_help(monkeypatch):
    """Ответ «Открыл …, но плеер не загрузился» модель читала как «почти
    получилось» и звала инструмент снова: в живом журнале семь заходов по
    18 секунд подряд, 27 секунд молчания и убитая сессия Gemini."""
    _film_that_never_starts(monkeypatch)

    answer = vp.play_film("Железный человек")

    assert not answer.startswith("Открыл"), answer
    assert "не" in answer.lower() and "vk" in answer.lower()
    assert "повтор" in answer.lower(), "модель должна понять, что повторять бесполезно"


def test_same_film_is_not_retried_immediately(monkeypatch):
    calls = _film_that_never_starts(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(vp.time, "monotonic", lambda: now[0])

    first = vp.play_film("Железный человек")
    second = vp.play_film("Железный человек")

    assert len(calls) == 1, f"второй заход ходил в VK снова: {calls}"
    assert second == first

    now[0] += vp._FAIL_COOLDOWN_SEC + 1          # остыло — пробуем заново
    vp.play_film("Железный человек")
    assert len(calls) == 2


def test_another_film_is_still_tried(monkeypatch):
    calls = _film_that_never_starts(monkeypatch)
    monkeypatch.setattr(vp.time, "monotonic", lambda: 1000.0)

    vp.play_film("Железный человек")
    vp.play_film("Интерстеллар")

    assert len(calls) == 2, "чужой фильм не должен упираться в чужую неудачу"


# ── громкость — строго по тому, о чём сказано ────────────────────────────────

def test_music_volume_changes_spotify_not_film_nor_system(monkeypatch, fake_player):
    """«Громкость музыки на 100» во время фильма — громкость Spotify. Раньше
    при музыке на паузе команда уходила фильму, а без Spotify — в системную."""
    from actions import music_player as mp
    from actions import spotify_premium
    from core import media_session
    calls = []
    monkeypatch.setattr(vp, "video_playing", lambda: True)
    monkeypatch.setattr(mp, "_music_playing", lambda: False)
    monkeypatch.setattr(media_session, "app_volume",
                        lambda proc, mode, step=10, target=None: calls.append((proc, mode, step, target)) or 100)
    assert mp.music_player({"action": "volume_up", "value": "100"}) == "Громкость Spotify 100%."
    assert calls == [("spotify.exe", "up", 100, None)]
    assert fake_player["vol"] == 0.5                                  # фильм не тронут

    import actions.computer_settings as cs
    monkeypatch.setattr(media_session, "app_volume", lambda *a, **k: None)   # Spotify не запущен
    monkeypatch.setattr(spotify_premium, "control", lambda action, value=None: None)
    monkeypatch.setattr(cs, "_volume", lambda *a, **k: pytest.fail("системную громкость трогать нельзя"))
    assert "Spotify сейчас не играет" in mp.music_player({"action": "volume_set", "value": "80"})


def test_film_volume_up_by_100_is_max(fake_player):
    assert vp.control("volume_up", "100") == "Громкость видео 100%."
    assert vp.control("volume_set", "пятьдесят") == "Громкость видео 50%."



def test_kinogo_result_tracks_confirmed_playback_independently_of_fullscreen(monkeypatch):
    monkeypatch.setattr(cdp, "ensure_browser", lambda **_: True)
    url = vp._KINOGO_HOME + "film.html"
    for state, expected in (
        (None, "error"), ({"paused": True}, "error"),
        ({"paused": False, "fs": False}, "partial"),
        ({"paused": False, "fs": True}, "success"),
    ):
        monkeypatch.setattr(vp, "_start", lambda *a, **kw: state)
        launch = vp.MovieLaunch()
        vp.play_kinogo(url, launch=launch)
        assert launch.outcome == expected



def test_kinogo_selects_card_with_requested_season():
    series = vp._KINOGO_HOME + "series.html"
    wrong = vp._KINOGO_HOME + "first-season.html"
    assert vp._kinogo_matches("Менталист", [
        {"url": series, "title": "Менталист (1-7 сезон)"},
        {"url": wrong, "title": "Менталист (1 сезон)"},
        {"url": vp._KINOGO_HOME + "different.html", "title": "Менталистка (1-7 сезон)"},
    ], season=3) == {series}


def test_kinogo_any_mirror_is_kinogo():
    """28.11: Kinogo переехал kz5 → kz6, серии перестали запускаться."""
    from actions import video_player as v
    assert v._kinogo_page("https://kz6.kinogo-films.lol/5059-igra-prestolov.html")
    assert v._kinogo_page("https://kz5.kinogo-films.lol/5059-igra-prestolov.html")
    assert not v._kinogo_page("https://kz6.kinogo-films.lol.evil.com/x")
    assert not v._kinogo_page("https://evil.com/5059.html")
    assert "kz\d+" in v._KINOGO_SERIES_PROVIDER and "__KINOGO" not in v._KINOGO_SERIES_PROVIDER


def test_resume_asks_which_when_several_recent(monkeypatch):
    """28.11: смотрю три сериала — «продолжи сериал» должен спросить, какой."""
    from actions import video_player as v
    now = 1_000_000.0
    rows = [{"title": "Игра престолов", "url": "u1", "season": 1, "episode": 4, "position": 470,
             "duration": 3000, "at": now - 3600},
            {"title": "Менталист", "url": "u2", "season": 3, "episode": 5, "position": 60,
             "duration": 2500, "at": now - 7200},
            {"title": "Старый", "url": "u3", "season": 1, "episode": 1, "position": 10,
             "duration": 2500, "at": now - 30 * 86400}]
    monkeypatch.setattr(v, "_watch_history", lambda: rows)
    monkeypatch.setattr(v.time, "time", lambda: now)
    monkeypatch.setattr(v, "_save_current_watch", lambda: None)
    text = v.series_context("series_resume", "")
    assert "Игра престолов" in text and "Менталист" in text and "Старый" not in text
    assert "какой продолжить" in text
    monkeypatch.setattr(v, "_watch_history", lambda: rows[:1] + rows[2:])
    assert v._which_series() == ""                                 # один свежий — сразу включаем


def test_series_card_found_without_season():
    """28.11 03:08: «включи Игру престолов» — «не нашёл карточку» из-за «(1-8 сезон)»."""
    from actions import video_player as v
    links = [{"url": "https://kz6.kinogo-films.lol/5059-igra-prestolov.html", "title": "Игра престолов (1-8 сезон)",
              "alternate": "", "year": "2011"},
             {"url": "https://kz6.kinogo-films.lol/5052-igra-prestolov-poslednij-dozor-2019.html",
              "title": "Игра престолов: Последний дозор (2019)", "alternate": "", "year": "2019"}]
    assert v._kinogo_matches("Игра Престолов", links) == {"https://kz6.kinogo-films.lol/5059-igra-prestolov.html"}
    assert v._kinogo_matches("Игра Престолов", links, season=3) == {"https://kz6.kinogo-films.lol/5059-igra-prestolov.html"}
    assert v._kinogo_matches("Игра Престолов", links, season=9) == set()


def test_generic_series_title_means_last(monkeypatch):
    """28.11 03:09: «продолжи мой сериал» → title «мой» → искал сериал «мой» на Kinogo."""
    from actions import video_player as v
    monkeypatch.setattr(v, "_save_current_watch", lambda: None)
    monkeypatch.setattr(v, "_which_series", lambda: "СПРОСИ")
    assert v.series_context("series_continue", "мой") == "СПРОСИ"
    assert v.series_context("series_resume", "этот сериал") == "СПРОСИ"

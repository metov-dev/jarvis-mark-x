from types import SimpleNamespace

import pytest

from actions import video_player as video
from core import dialog_context, media_session, quick, win_apps


@pytest.mark.parametrize(("phrase", "tool", "action"), [
    ("поставь это на паузу", "video_control", "pause"),
    ("на чём я остановился", "desktop_context", "resume"),
    ("над чем я работала", "desktop_context", "resume"),
])
def test_contextual_commands_are_available_without_cloud(monkeypatch, phrase, tool, action):
    monkeypatch.setattr(quick, "enabled", lambda: True)
    command = quick.match(phrase, offline=True)
    assert (command.tool, command.args["action"]) == (tool, action)
    if action == "pause":
        assert command.args["target"] == "context"


def test_work_context_retains_work_across_a_game_and_expires():
    work = dialog_context.WorkContext()
    work.observe({"application": "code.exe", "window": "Проект A"}, 10)
    work.observe({"application": "cs2.exe", "window": "Игра"}, 20)
    assert "Проект A" in work.describe(21)
    work.observe({"application": "excel.exe", "window": "Таблица B"}, 25)
    assert "Таблица B" in work.describe(26)
    assert "не видел" in work.describe(3626)


@pytest.fixture
def players(monkeypatch):
    tab = SimpleNamespace(_embedded_video=True, eval=lambda _: "Передача - YouTube")
    spotify = media_session.NowPlaying("spotify.exe", "Песня", "", True)
    state = SimpleNamespace(embedded=True, paused=False, os_players=[], window=None)
    actions = []
    monkeypatch.setattr(video.cdp, "has_embedded_video_target", lambda: state.embedded)
    monkeypatch.setattr(video.cdp, "tab", lambda create: tab)
    monkeypatch.setattr(video.cdp, "video_state",
                        lambda selected: {"paused": state.paused, "ready": 4} if selected is tab else None)
    monkeypatch.setattr(video.cdp, "browser_exe", lambda: r"C:\Apps\browser.exe")
    monkeypatch.setattr(video.cdp, "video_js",
                        lambda body, t: actions.append(("video", t)) or {"paused": True})
    monkeypatch.setattr(media_session, "playing_sessions", lambda: state.os_players)
    monkeypatch.setattr(media_session, "pause_matching", lambda session: actions.append(("windows", session)) or True)
    monkeypatch.setattr(win_apps, "foreground", lambda: state.window)
    return state, tab, spotify, actions


def test_same_embedded_clip_is_not_counted_twice(players):
    state, tab, _, actions = players
    state.os_players = [media_session.NowPlaying("browser.exe", "Передача", "", True)]
    assert video.pause_current() == "Видео на паузе."
    assert actions == [("video", tab)]


def test_background_video_does_not_override_front_spotify(players):
    state, _, spotify, actions = players
    state.window = SimpleNamespace(exe="spotify.exe")
    state.os_players = [spotify]
    assert "паузу" in video.pause_current()
    assert actions == [("windows", spotify)]


def test_ambiguous_or_unavailable_source_does_not_change_playback(players):
    state, _, spotify, actions = players
    state.os_players = [spotify]
    assert "несколько" in video.pause_current()
    state.os_players = None
    assert "Не удалось" in video.pause_current()
    assert actions == []


def test_paused_browser_leaves_the_only_playing_session(players):
    state, _, spotify, actions = players
    state.paused = True
    state.os_players = [spotify]
    video.pause_current()
    assert actions == [("windows", spotify)]


def test_windows_pause_revalidates_session_before_changing_it(monkeypatch):
    import asyncio

    calls = []

    class Session:
        source_app_user_model_id = "spotify.exe"

        def get_playback_info(self):
            return SimpleNamespace(playback_status=media_session._PLAYING)

        async def try_get_media_properties_async(self):
            return SimpleNamespace(title="Новая песня", artist="")

        async def try_pause_async(self):
            calls.append("pause")
            return True

    sessions = [Session()]

    async def manager():
        return SimpleNamespace(get_sessions=lambda: sessions)

    monkeypatch.setattr(media_session, "_manager", manager)
    old = media_session.NowPlaying("spotify.exe", "Старая песня", "", True)
    new = media_session.NowPlaying("spotify.exe", "Новая песня", "", True)
    assert not asyncio.run(media_session._pause_matching(old))
    sessions.append(Session())
    assert not asyncio.run(media_session._pause_matching(new))
    sessions.pop()
    assert asyncio.run(media_session._pause_matching(new))
    assert calls == ["pause"]


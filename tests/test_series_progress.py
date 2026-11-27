from types import SimpleNamespace

import pytest

from actions import video_player as vp
from actions.movie_player import movie_player
from core import quick
from core.dialog_context import DialogContext


@pytest.fixture
def active_episode(tmp_path, monkeypatch):
    save_path = tmp_path / "watch_progress.json"
    state = {"t": 824.9, "d": 2600, "paused": False,
             "season": 3, "episode": 5, "source": "episode-5"}
    url = vp._KINOGO_HOME + "mentalist.html"
    frame = SimpleNamespace(session_id="player-frame")
    tab = SimpleNamespace(_film_player=frame, _film_player_required=True)
    tab.eval = lambda *args, **kwargs: url
    tab.video_frame = lambda selector: frame
    tab.call = lambda *args, **kwargs: {"result": {"value": dict(state)}}
    monkeypatch.setattr(vp, "_watch_file", lambda: save_path)
    monkeypatch.setattr(vp, "_watching", None)
    monkeypatch.setattr(vp.threading, "Thread", lambda **kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(vp.cdp, "tab", lambda **kwargs: tab)
    monkeypatch.setattr(vp.cdp, "video_state", lambda t: state)
    monkeypatch.setattr(vp.cdp, "pin_film_player", lambda *args, **kwargs: True)
    vp._track_series(tab, "Менталист", url, (3, 5))
    return state, url


def test_resume_after_restart_uses_stored_url_episode_and_position(active_episode, monkeypatch):
    _, url = active_episode
    monkeypatch.setattr(vp, "_watching", None)
    calls = []
    def play(source, ui, launch, **kwargs):
        calls.append((source, kwargs))
        return "Продолжил."
    monkeypatch.setattr(vp, "play_kinogo", play)
    assert movie_player({"action": "series_resume"}) == "Продолжил."
    assert calls == [(url, {"episode": (3, 5), "watch_title": "Менталист", "position": 824})]
    assert "13:44" in movie_player({"action": "series_position"})


def test_pause_checkpoint_and_unrelated_episode_cannot_overwrite_bookmark(active_episode, monkeypatch):
    state, _ = active_episode
    state["t"] = 960
    assert vp._save_current_watch()
    state["episode"] = 6
    assert vp._save_current_watch() is None
    assert vp._bookmark()["position"] == 960
    assert vp._bookmark()["episode"] == 5


def test_successive_episodes_and_last_position_survive_close(active_episode, monkeypatch):
    state, url = active_episode
    for episode in range(6, 10):
        state.update(episode=episode, t=10, source=f"episode-{episode}")
        assert vp._save_current_watch()
        assert vp._bookmark()["episode"] == episode
    state.update(t=151.9, paused=True)
    vp.save_watch_progress()
    monkeypatch.setattr(vp, "_watching", None)
    row = vp._bookmark()
    assert isinstance(row.pop("at"), float)                     # когда смотрели (для «какой продолжить?»)
    assert row == {"title": "Менталист", "url": url, "season": 3,
                   "episode": 9, "position": 151, "duration": 2600}


def test_transition_retries_until_the_new_stream_has_loaded(active_episode):
    state, _ = active_episode
    state.update(episode=6, paused=True)
    assert vp._checkpoint(vp._watching) is False
    state.update(source="episode-6", t=0, paused=False)
    assert vp._checkpoint(vp._watching) is True
    assert vp._bookmark()["episode"] == 6


def test_next_and_change_season_routed_to_right_search(active_episode, monkeypatch):
    _, url = active_episode
    calls = []
    monkeypatch.setattr(vp, "play_kinogo",
                        lambda source, ui, launch, **kwargs: calls.append((source, kwargs["episode"])))
    movie_player({"action": "series_next"})
    movie_player({"action": "series_episode", "season": 4, "episode": 2})
    assert calls == [(url, (3, 6)), ("Менталист", (4, 2))]


def test_continue_in_active_tab_does_not_reload_it(active_episode, monkeypatch):
    state, _ = active_episode
    calls = []
    monkeypatch.setattr(vp.cdp, "video_js", lambda *args: state)
    monkeypatch.setattr(vp, "play_kinogo", lambda *args, **kwargs: calls.append(args))
    assert "Продолжаю" in movie_player({"action": "series_resume"})
    assert not calls


def test_resume_at_end_offers_next_and_named_resume_does_not_open_other_title(active_episode):
    state, _ = active_episode
    state["t"] = 2598
    assert "следующая серия" in movie_player({"action": "series_resume"})
    assert "нет сохранённого" in movie_player({"action": "series_resume", "title": "Шерлок"})


@pytest.mark.parametrize(("text", "action"), [
    ("Джарвис, продолжи с того места, где я остановился", "series_resume"),
    ("Джарвис, продолжи сериал Менталист", "series_resume"),
    ("Где я остановился?", "series_position"),
    ("Следующая серия", "series_next"),
    ("Поставь 7-ую серию", "series_episode"),
    ("Поставь сериал Менталист", "series_continue"),
])
def test_series_shortcuts_work_without_gemini(text, action, monkeypatch):
    monkeypatch.setattr(quick, "enabled", lambda: True)
    hit = quick.match(text, offline=True)
    assert hit.tool == "movie_player"
    assert hit.args["action"] == action


def test_vague_followup_is_scoped_to_the_current_topic():
    dialog = DialogContext(ttl=12)
    assert dialog.resolve("а дальше", now=1) is None
    dialog.record("продолжи сериал Менталист", "movie_player",
                  {"action": "series_resume", "title": "менталист"}, timestamp=1)
    assert dialog.resolve("а дальше", now=3).args == {"action": "series_next", "title": "менталист"}
    assert dialog.resolve("а дальше", now=14) is None
    dialog.record("поставь музыку", "music_player", {"action": "play"}, timestamp=15)
    assert dialog.resolve("дальше", now=16).tool == "music_player"



def test_poll_survives_dropped_cdp_websocket(active_episode, monkeypatch):
    """Лог 23.11 22:52: обрыв websocket (ConnectionClosedError) убивал поток
    сохранения, и позиция серии переставала записываться."""
    from websockets.exceptions import ConnectionClosedError
    watch = vp._watching
    calls = []

    def checkpoint(w):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionClosedError(None, None)
        if len(calls) == 3:
            vp._watching = None
        return True

    monkeypatch.setattr(vp, "_checkpoint", checkpoint)
    monkeypatch.setattr(vp.time, "sleep", lambda s: None)
    vp._poll_watch(watch)
    assert len(calls) == 3


def test_command_in_wake_window_is_addressed_even_if_name_garbled():
    """Лог 23.11 22:54–22:55: «Джарвис» услышан как «Чарли», вопрос получил отказ;
    но 23:33 Gemini сам вызвал brightness_set — системные инструменты окно не открывает."""
    import main
    assert main.wake_command_allows(False, True, "web_search")
    assert main.wake_command_allows(False, True, "video_control")
    for risky in ("computer_control", "files", "open_app", "phone_call", "message", "window_control"):
        assert not main.wake_command_allows(False, True, risky), risky
    assert main.wake_command_allows(True, False, "computer_control")
    assert not main.wake_command_allows(False, False, "web_search")
    assert not main.wake_command_allows(False, True, "computer_control")

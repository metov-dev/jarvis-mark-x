from types import SimpleNamespace

import pytest

from core import dialog_context, quick, wake_kws
from core.wake_vosk import address_reason, split_address


@pytest.mark.parametrize("text", [
    "Джарвис, прибавь громкость",
    "Джорвис, прибавь громкость",
    "Чарли, прибавь громкость",
    "Прибавь громкость, Чарли",
])
def test_direct_address_routes_only_command_payload(monkeypatch, text):
    monkeypatch.setattr(quick, "enabled", lambda: True)
    name, command = split_address(text)
    assert name and command.casefold() == "прибавь громкость"
    assert address_reason(text) == "name"
    intent = quick.match(command, offline=True)
    assert (intent.tool, intent.args["action"]) == ("computer_control", "volume_up")


@pytest.mark.parametrize("text", [
    "Мы вчера обсуждали Джарвиса",
    "Джарвис хороший помощник",
    "Я говорил Чарли прибавить громкость",
    "Он сейчас прибавит громкость",
    "Мы виделись, Чарли",
])
def test_mention_has_no_authority_even_inside_listening_window(text):
    assert not address_reason(text, followup=True, final=True)


def test_second_name_and_standalone_name_boundaries():
    assert not address_reason("Чарли, прибавь громкость", allow_alias=False)
    assert not address_reason("Джарвис")
    assert address_reason("Джарвис", final=True) == "name"
    assert not split_address("Джарвисов")[0]


def test_followup_requires_confirmed_context():
    assert not address_reason("Ещё немного", quick_match=True)
    assert address_reason("Ещё немного", quick_match=True, followup=True) == "followup"
    ctx = dialog_context.DialogContext()
    ctx.record("чуть тише", "computer_control", {"action": "volume_down"})
    result = ctx.resolve("ещё немного")
    assert result.args["action"] == "volume_down"


def test_ru_model_has_valid_tokenizations_for_aliases():
    model = wake_kws.find_model()
    if model is None:
        pytest.skip("Для проверки токенов требуется локальная модель")
    vocab = {row.split()[0] for row in (model / "tokens.txt").read_text(encoding="utf-8").splitlines()}
    lines = wake_kws._keyword_lines(model, allow_alias=True)
    labels = {line.split("@")[-1] for line in lines}
    assert {"ДЖОРВИС", "ЧАРЛИ"} <= labels
    assert all(set(line.split("@")[0].split()) <= vocab for line in lines)
    assert not any(line.endswith("@ЧАРЛИ") for line in wake_kws._keyword_lines(model, False))


def test_context_uses_current_window_and_only_playing_media(monkeypatch):
    from core import media_session, win_apps

    win = SimpleNamespace(exe=r"C:\Apps\Code.exe", title="app.py — Jarvis")
    media = SimpleNamespace(playing=True, app="Browser", title="Менталист")
    monkeypatch.setattr(win_apps, "foreground", lambda: win)
    monkeypatch.setattr(media_session, "now_playing", lambda: media)
    assert dialog_context.desktop_context() == {
        "application": "Code.exe", "window": "app.py — Jarvis",
        "playing": {"application": "Browser", "title": "Менталист"},
    }
    win.exe = r"C:\Apps\Jarvis.exe"
    media.playing = False
    assert dialog_context.desktop_context() == {}


def test_observer_logs_only_a_project_resume_after_break():
    observer = dialog_context.ResumeObserver()
    editor = {"application": "Code.exe", "window": "app.py — Jarvis"}
    assert not observer.observe(editor, 0)
    assert not observer.observe({}, 60)
    assert not observer.observe(editor, 90)
    assert not observer.observe({}, 100)
    assert observer.observe(editor, 750)
    assert not observer.observe(editor, 755)


def test_followup_does_not_admit_a_room_conversation_question():
    assert not address_reason("Что ты сегодня ел?", followup=True, final=True)


def test_playback_reference_separates_a_copy_from_overlapping_voice():
    import numpy as np
    from core.audio_capture import playback_matches

    rng = np.random.default_rng(214)
    reference = rng.integers(-4000, 4000, size=4096).astype(np.float32)
    for lag in range(416, 424):
        echo = reference[lag:lag + 1024] * 0.4
        nearby_voice = rng.integers(-1600, 1600, size=len(echo))
        assert playback_matches(echo, reference)
        assert not playback_matches(echo + nearby_voice, reference)
    assert not playback_matches(np.zeros(1024), reference)
    assert not playback_matches(np.zeros(1025), reference[:1001])



def test_only_current_acoustic_name_can_repair_an_asr_error():
    from core.wake_vosk import repair_address

    assert repair_address("Джарвист, прибавь громкость", "джарвис") == "Джарвис, прибавь громкость"
    assert repair_address("Джойс прибавь громкость", "джорвис") == "Джорвис прибавь громкость"
    assert repair_address("Джойс прибавь громкость", "чарли") == "Джойс прибавь громкость"
    assert repair_address("Мы обсуждали Джарвиста", "джарвис") == "Мы обсуждали Джарвиста"




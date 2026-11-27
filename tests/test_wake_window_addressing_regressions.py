import pytest

from main import wake_command_allows
from core.wake_vosk import address_reason


@pytest.mark.parametrize("text", [
    "Я хотел спросить, что мы обсуждали вчера",
    "Мне нужно продолжить то, что мы делали",
    "Слушай, помнишь название того сериала?",
])
def test_confirmed_wake_accepts_natural_following_speech(text):
    assert address_reason(text, after_wake=True) == "wake_command"


@pytest.mark.parametrize("text", ["", "а", "эээ", "угу", "ну"])
def test_wake_window_does_not_accept_empty_or_filler_audio(text):
    assert not address_reason(text, after_wake=True)


def test_without_wake_or_direct_address_unrelated_speech_stays_unaddressed():
    text = "Я хотел спросить, что мы обсуждали вчера"
    assert not address_reason(text)
    assert not address_reason(text, followup=True, final=True)


def test_wake_window_allows_routine_pc_actions_only_with_a_real_request():
    assert not wake_command_allows(False, True, "computer_control")
    assert not wake_command_allows(False, True, "computer_control", "угу")
    assert wake_command_allows(False, True, "open_app", "Открой мой браузер")
    assert wake_command_allows(False, True, "computer_control", "Сделай яркость выше")
    assert not wake_command_allows(False, True, "files", "Открой файл")
    assert not wake_command_allows(False, True, "contacts", "Позвони Саше")

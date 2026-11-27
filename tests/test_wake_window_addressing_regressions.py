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


@pytest.mark.parametrize("text", ["Да, который час?", "Который час?", "А какая погода завтра?",
                                  "Ну открой ютуб"])
def test_followup_question_right_after_answer_is_addressed(text):
    """Журнал 27.11 23:31: второй вопрос сразу после ответа — отказ «не ко мне»."""
    assert address_reason(text, followup=True, final=True, just_answered=True) == "followup"


@pytest.mark.parametrize("text", ["Да, который час?", "ну и что он тебе сказал"])
def test_followup_rules_need_open_conversation_and_clear_request(text):
    assert not address_reason(text)
    assert not address_reason(text, followup=True, final=True)          # без «только что ответил»
    assert not address_reason("Я думаю, он прав", just_answered=True)   # не вопрос и не просьба


def test_name_with_misspelled_verb_is_addressed():
    """Журнал 27.11 23:49: «Jarvis, открои Instagram.» — отказ «не ко мне»."""
    assert address_reason("Jarvis, открои Instagram.", final=True) == "name"
    assert address_reason("Джарвис, закрои вкладку", final=True) == "name"

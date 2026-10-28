"""Тесты контекстной памяти диалога (Multi-turn follow-ups)."""
import pytest

from core.dialog_context import DialogContext
from core import quick as q


@pytest.fixture
def ctx():
    c = DialogContext(ttl=30.0)
    c.clear()
    return c


def test_weather_follow_up_tomorrow(ctx, monkeypatch):
    from core import dialog_context
    monkeypatch.setattr(dialog_context, "context", lambda: ctx)

    # 1. Спросили погоду в Москве
    ctx.record("какая погода в москве", "weather", {"city": "москве"}, "В Москве сейчас +15")

    # 2. Уточнение «А завтра?»
    res = q.match("а завтра?")
    assert res is not None
    assert res.tool == "weather"
    assert res.args.get("city") == "москве"
    assert res.args.get("day") == "tomorrow"


def test_weather_follow_up_new_city(ctx, monkeypatch):
    from core import dialog_context
    monkeypatch.setattr(dialog_context, "context", lambda: ctx)

    ctx.record("какая погода в москве", "weather", {"city": "москве"}, "В Москве сейчас +15")

    res = q.match("а в ташкенте?")
    assert res is not None
    assert res.tool == "weather"
    assert res.args.get("city") == "ташкенте"


def test_weather_follow_up_weekend(ctx, monkeypatch):
    from core import dialog_context
    monkeypatch.setattr(dialog_context, "context", lambda: ctx)

    ctx.record("погода в питере", "weather", {"city": "питере"}, "В Питере дождь")

    res = q.match("на выходных")
    assert res is not None
    assert res.tool == "weather"
    assert res.args.get("city") == "питере"
    assert res.args.get("day") == "weekend"


def test_timer_follow_up_cancel_and_modify(ctx, monkeypatch):
    from core import dialog_context
    monkeypatch.setattr(dialog_context, "context", lambda: ctx)

    # 1. Завели таймер
    ctx.record("поставь таймер на 10 минут", "clock", {"action": "timer_set", "duration": "10 минут"}, "Поставил таймер на 10 минут.")

    # 2. «Сделай 15» (без слова минут)
    res_mod = q.match("сделай 15")
    assert res_mod is not None
    assert res_mod.tool == "clock"
    assert res_mod.args.get("action") == "timer_modify"
    assert res_mod.args.get("duration") == "15 минут"

    # 3. «Отмени» (без слова таймер)
    res_cancel = q.match("отмени")
    assert res_cancel is not None
    assert res_cancel.tool == "clock"
    assert res_cancel.args.get("action") == "timer_cancel"

    # 4. «Добавь 5 минут»
    res_add = q.match("добавь 5 минут")
    assert res_add is not None
    assert res_add.tool == "clock"
    assert res_add.args.get("action") == "timer_add"
    assert res_add.args.get("duration") == "5 минут"

    # 5. «Сколько осталось»
    res_list = q.match("сколько осталось")
    assert res_list is not None
    assert res_list.tool == "clock"
    assert res_list.args.get("action") == "timer_list"


def test_volume_follow_up_repeat(ctx, monkeypatch):
    from core import dialog_context
    monkeypatch.setattr(dialog_context, "context", lambda: ctx)

    ctx.record("сделай тише", "computer_control", {"action": "volume_down"}, "Убавил, сэр.")

    res = q.match("еще")
    assert res is not None
    assert res.tool == "computer_control"
    assert res.args.get("action") == "volume_down"

    res_stop = q.match("хватит")
    assert res_stop is not None
    assert res_stop.reply == "ack"


def test_music_follow_up(ctx, monkeypatch):
    from core import dialog_context
    monkeypatch.setattr(dialog_context, "context", lambda: ctx)

    ctx.record("включи музыку", "music_player", {"action": "play"}, "Играет...")

    res_next = q.match("дальше")
    assert res_next is not None
    assert res_next.tool == "music_player"
    assert res_next.args.get("action") == "next"

    res_who = q.match("кто поет")
    assert res_who is not None
    assert res_who.tool == "music_player"
    assert res_who.args.get("action") == "now_playing"


def test_context_expiration(ctx, monkeypatch):
    from core import dialog_context
    monkeypatch.setattr(dialog_context, "context", lambda: ctx)

    # Запись с меткой времени 100.0
    ctx.record("сделай тише", "computer_control", {"action": "volume_down"}, "Убавил", timestamp=100.0)

    # Проверяем в момент 110.0 (прошло 10 секунд — в пределах TTL=30)
    assert ctx.resolve("еще", now=110.0) is not None

    # Проверяем в момент 140.0 (прошло 40 секунд — истекло)
    assert ctx.resolve("еще", now=140.0) is None

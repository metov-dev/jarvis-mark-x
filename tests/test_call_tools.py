"""Звонок 28.11 01:09: «будильник на 8:00 установлен», «перезвоню через минуту» —
а в звонке был только end_call, ничего не сделано."""
from core import tg_call


def test_call_back_schedules_a_real_call(monkeypatch):
    added = []
    monkeypatch.setattr(tg_call, "schedule", lambda: type("S", (), {"add": lambda self, *a: added.append(a)})())
    res = tg_call.run_call_tool("call_back", {"minutes": 1})
    assert "запланирован" in res and added and added[0][2] == "once"


def test_call_back_rejects_nonsense(monkeypatch):
    monkeypatch.setattr(tg_call, "schedule", lambda: (_ for _ in ()).throw(AssertionError("не должен звать")))
    assert "Не понял" in tg_call.run_call_tool("call_back", {"minutes": 0})


def test_clock_runs_through_jarvis_and_other_tools_are_refused(monkeypatch):
    ran = []
    tg_call.set_tools(lambda name, args: ran.append((name, args)) or "Будильник на 08:00 поставлен.",
                      [{"name": "clock"}])
    try:
        assert "поставлен" in tg_call.run_call_tool("clock", {"action": "alarm_set", "time": "08:00"})
        assert ran == [("clock", {"action": "alarm_set", "time": "08:00"})]
        assert "недоступно" in tg_call.run_call_tool("files", {"action": "delete"})
    finally:
        tg_call.set_tools(None, [])


def test_live_config_offers_the_tools(monkeypatch):
    tg_call.set_tools(lambda n, a: "", [{"name": "clock", "description": "", "parameters": {"type": "OBJECT"}}])
    try:
        names = {d["name"] for d in [tg_call.END_CALL, tg_call.CALL_BACK, *tg_call._tool_declarations]}
        assert {"end_call", "call_back", "clock"} <= names
        assert "call_back" in tg_call.instruction("тест")
    finally:
        tg_call.set_tools(None, [])

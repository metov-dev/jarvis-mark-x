from datetime import datetime, timedelta
from unittest.mock import Mock

from actions import sleep_timer as sleep
from core import calendar_manager, quick
from core.break_reminder import Activity, BreakReminder
from core.clock import Clock, parse_duration
from core.tg_call import Schedule


def test_offline_pause_uses_pause_handler(monkeypatch):
    monkeypatch.setattr(quick, "enabled", lambda: True)
    result = quick.match("таймер на паузу", offline=True)
    assert result and result.args["action"] == "timer_pause"


def test_invalid_timer_durations_are_rejected():
    for duration in ("-5 минут", "1:99", "0:00", "1:01:60"):
        assert parse_duration({"duration": duration}) is None
    assert parse_duration({"hours": 1, "minutes": -1}) is None


def test_missed_alarm_is_reported_once_and_leaves_active_list(tmp_path):
    now = [datetime(2026, 11, 23, 6, 55)]
    path = tmp_path / "clock.json"
    clock = Clock(path, now=lambda: now[0].timestamp(), local=lambda: now[0])
    clock.alarm_set("07:00", "встреча")
    now[0] += timedelta(minutes=10)
    reopened = Clock(path, now=lambda: now[0].timestamp(), local=lambda: now[0])
    reopened.sound = Mock()
    reopened.notify = Mock()
    reopened.tick()
    reopened.tick()
    reopened.notify.assert_called_once_with("ПРОПУЩЕН БУДИЛЬНИК", "07:00 — встреча")
    reopened.sound.play.assert_not_called()
    assert not reopened.alarms
    assert not Clock(path).alarms


def test_stopwatch_keeps_running_after_crash_reopen(tmp_path):
    now = [2000.0]
    path = tmp_path / "clock.json"
    clock = Clock(path, now=lambda: now[0])
    clock.sw_start()
    now[0] += 12
    clock.tick()
    now[0] += 3600                                  # процесс упал, stop() не вызван
    reopened = Clock(path, now=lambda: now[0])
    assert reopened.stopwatch.started
    assert reopened.stopwatch.value(now[0]) == 3612


def test_stopwatch_old_format_freezes_at_last_value(tmp_path):
    import json
    path = tmp_path / "clock.json"
    path.write_text(json.dumps({"timers": [], "alarms": [], "next_id": 1,
                                "stopwatch": {"started": 2000.0, "elapsed": 5.0, "laps": [12.0]}}),
                    encoding="utf-8")
    reopened = Clock(path, now=lambda: 9999.0)
    assert not reopened.stopwatch.started
    assert reopened.stopwatch.elapsed == 12


def test_due_reminders_persist_delivery_and_skip_corrupt_records(tmp_path, monkeypatch):
    import json
    path = tmp_path / "calendar.json"
    monkeypatch.setattr(calendar_manager, "CALENDAR_FILE", path)
    now = datetime(2026, 11, 23, 7, 5)
    records = [
        {"datetime": "bad", "text": "bad", "completed": False},
        {"datetime": "2026-11-23T07:00:00", "text": "чай", "completed": False},
        {"datetime": "2026-11-23T07:05:00", "text": "встреча", "completed": False},
        {"datetime": "2026-11-23T07:06:00", "text": "будущее", "completed": False},
    ]
    path.write_text(json.dumps({"events": [], "reminders": records}), encoding="utf-8")
    assert [r["text"] for r in calendar_manager.take_due_reminders(now)] == ["чай", "встреча"]
    assert calendar_manager.take_due_reminders(now) == []
    assert [r["text"] for r in calendar_manager.take_due_reminders(now + timedelta(minutes=1))] == ["будущее"]


def test_missed_calls_removed_same_day_and_recent_call_still_due():
    now = datetime(2026, 11, 23, 6, 55)
    schedule = Schedule(None)
    schedule.add("07:00", "пропущен", now=now)
    schedule.add("07:10", "вовремя", now=now)
    assert [c["topic"] for c in schedule.due(now.replace(minute=12, hour=7))] == ["вовремя"]
    assert schedule.items == []


def test_break_restarts_when_activity_changes():
    now = [0.0]
    state = [Activity("video", "фильм")]
    reminder = BreakReminder(Mock(), sense=lambda: state[0], clock=lambda: now[0])
    reminder.first_min = 1
    reminder.tick()
    now[0] = 59
    state[0] = Activity("game", "игра")
    reminder.tick()
    now[0] = 60
    assert reminder.tick() is None
    now[0] = 119
    assert "играет" in reminder.tick()


def test_bad_sleep_timer_value_does_not_touch_existing_timer(monkeypatch):
    manager = Mock()
    monkeypatch.setattr(sleep, "sleep_timer_manager", manager)
    for minutes in ("?", 0, -1, float("nan"), float("inf")):
        result = sleep.sleep_timer({"action": "set", "duration_minutes": minutes})
        assert "Укажите" in result
    assert manager.mock_calls == []


"""Капсула: «Звонок в 09:00» — только будущий звонок; прошедший разовый убирается."""
import json
from datetime import datetime

from core import tg_call


def _sched(tmp_path, items):
    p = tmp_path / "call_schedule.json"
    p.write_text(json.dumps(items), encoding="utf-8")
    return tg_call.Schedule(str(p)), p


def test_прошедший_разовый_звонок_удаляется(tmp_path):
    s, p = _sched(tmp_path, [{"id": 1, "time": "09:00", "repeat": "once", "date": "2026-10-10", "topic": "x", "last": ""}])
    assert s.items == [] and json.loads(p.read_text(encoding="utf-8")) == []
    assert s.upcoming() is None


def test_ближайший_будущий(tmp_path):
    s, _ = _sched(tmp_path, [
        {"id": 1, "time": "06:00", "repeat": "daily", "topic": "a", "last": ""},
        {"id": 2, "time": "23:00", "repeat": "once", "date": "2099-01-01", "topic": "b", "last": ""},
    ])
    now = datetime(2026, 11, 20, 7, 0)
    assert s.upcoming(now)["id"] == 1          # ежедневный — завтра в 06:00, раньше 2099 года

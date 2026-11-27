"""Сервер бота не должен будить базу без дела (Neon free: лимит кончился 24.11)."""
import asyncio

import pytest

from telegram_bot import proactive, reminders


class _Mem:
    def __init__(self, due):
        self.queries = 0
        self.reminders_dirty = True
        self._due = due

    async def get_due_reminders(self, now_iso):
        self.queries += 1
        return []

    async def next_reminder_due(self):
        self.queries += 1
        return self._due


async def _run_loop(mem, ticks=20):
    task = asyncio.create_task(reminders.delivery_loop(None, mem, _Log(), every=0.001))
    await asyncio.sleep(0.001 * ticks + 0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)  # цикл сам ловит отмену


class _Log:
    def warning(self, *a): ...
    def error(self, *a): ...


@pytest.mark.asyncio
async def test_reminder_loop_sleeps_until_next_due():
    mem = _Mem("2999-01-01T00:00:00")
    await _run_loop(mem)
    assert mem.queries == 2                    # один заход: due + next, дальше тишина


@pytest.mark.asyncio
async def test_new_reminder_wakes_the_loop():
    mem = _Mem(None)
    task = asyncio.create_task(reminders.delivery_loop(None, mem, _Log(), every=0.001))
    await asyncio.sleep(0.03)
    first = mem.queries
    mem.reminders_dirty = True
    await asyncio.sleep(0.03)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)  # цикл сам ловит отмену
    assert first == 2 and mem.queries == 4


@pytest.mark.asyncio
async def test_proactive_caches_user_list_and_meta(monkeypatch):
    calls = {"uids": 0, "meta": 0}

    class M:
        async def all_user_ids(self):
            calls["uids"] += 1
            return [1]

        async def get_meta(self, uid, key):
            calls["meta"] += 1
            return "2026-11-27"

    monkeypatch.setattr(proactive, "_uids_cache", None)
    monkeypatch.setattr(proactive, "_meta_cache", {})
    m = M()
    for _ in range(5):
        assert await proactive._user_ids(m) == [1]
        assert await proactive._meta(m, 1, "briefing_morning") == "2026-11-27"
    assert calls == {"uids": 1, "meta": 1}


def test_pc_sync_skips_empty_exchanges(monkeypatch, tmp_path):
    from memory import shared
    monkeypatch.setattr(shared, "OUTBOX_FILE", tmp_path / "outbox.json")
    monkeypatch.setattr(shared, "configured", lambda: True)
    assert not shared.has_outgoing()
    shared._enqueue("turns", {"role": "user", "text": "привет", "ts": 1})
    assert shared.has_outgoing()

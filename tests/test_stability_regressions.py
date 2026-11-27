from unittest.mock import Mock

from actions import file_controller, modes, movie_player
from core import calendar_manager as calendar
from core import team_collaboration
from memory import conversation, memory_manager, shared


def test_calendar_requests_clarification_and_preserves_data_on_write_failure(monkeypatch):
    events = [
        {"id": "first", "title": "Встреча в среду", "start": "2026-11-25T10:00:00"},
        {"id": "second", "title": "Встреча в четверг", "start": "2026-11-26T10:00:00"},
    ]
    data = {"events": events}
    save = Mock(return_value=False)
    monkeypatch.setattr(calendar, "_load_calendar", lambda: data)
    monkeypatch.setattr(calendar, "_save_calendar", save)

    assert "Уточните" in calendar.delete_event("Встреча")
    assert "Уточните" in calendar.update_event("Встреча", new_duration="2 часа")
    assert "Уточните" in calendar.delete_event("  ")
    save.assert_not_called()

    assert "Не удалось сохранить" in calendar.delete_event("second")
    assert save.call_args.args[0]["events"] == [events[0]]
    assert "Не удалось сохранить" in calendar.update_event("first", new_duration="2 часа")
    assert data["events"] == events
    assert len(data["events"]) == 2
    assert "duration_minutes" not in events[0]


def test_failed_team_saves_do_not_leave_phantom_entries(tmp_path, monkeypatch):
    team = team_collaboration.TeamCollaborationEngine(tmp_path)
    assert team.add_project("Рабочий")
    with monkeypatch.context() as patches:
        patches.setattr(team_collaboration, "atomic_write_json", Mock(side_effect=OSError("disk full")))
        assert not team.add_project("Несохранённый")
        assert not team.add_team_member("Иван", "Разработчик")
        assert not team.add_task("Рабочий", "Несохранённая задача")

    assert team.get_project_status("Несохранённый") is None
    assert team.get_project_status("Рабочий")["total_tasks"] == 0
    assert not team.team_data["team_members"]
    assert team.add_task("Рабочий", "Повтор")
    restored = team_collaboration.TeamCollaborationEngine(tmp_path)
    assert restored.get_project_status("Рабочий")["total_tasks"] == 1


def test_movie_mode_retries_failed_playback(monkeypatch):
    state = {"mode": "movie", "preference": "Тест"}
    play = Mock(side_effect=["Плеер недоступен.", "Включил фильм на Kinogo во весь экран."])
    save = Mock()
    monkeypatch.setattr(modes, "get_current_mode", lambda: state)
    monkeypatch.setattr(modes, "_save_state", save)
    monkeypatch.setattr(movie_player, "movie_player", play)
    command = {"mode": "movie", "preference": "Тест"}
    assert modes.set_mode(command) == "Плеер недоступен."
    save.assert_not_called()
    assert modes.set_mode(command).startswith("Включил")
    assert play.call_count == 2
    save.assert_called_once_with("movie", "Тест")


def test_file_changed_after_confirmation_is_kept(tmp_path, monkeypatch):
    target = tmp_path / "report.txt"
    target.write_text("before")
    info = target.stat()
    identity = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)
    target.write_text("changed after confirmation")
    recycle = Mock()
    monkeypatch.setattr(file_controller, "_to_recycle_bin", recycle)

    assert file_controller.deletion_target(str(target)) == target.resolve()
    result = file_controller.file_controller(
        {"action": "delete", "path": str(target), "_file_identity": identity}
    )
    assert "объект изменился" in result
    recycle.assert_not_called()


def test_forget_question_lists_candidates_without_deleting(monkeypatch):
    memory = {"notes": {"музыка": "джаз", "музыка на работе": "рок", "город": "Астана"}}
    monkeypatch.setattr(memory_manager, "load_memory", lambda: memory)
    monkeypatch.setattr(shared, "configured", lambda: True)
    queue = Mock()
    monkeypatch.setattr(shared, "queue_forget", queue)

    assert "«музыка»" in conversation.forget_question("музыка")
    question = conversation.forget_question("музыка")
    assert "«музыка на работе»" in question
    assert "«город»" not in question
    assert "В общей памяти" in question
    assert len(memory["notes"]) == 3
    queue.assert_not_called()


def test_window_control_requires_unique_window_and_verifies_close(monkeypatch):
    from actions import window_control
    from core import win_apps

    report = win_apps.Window(1, "Отчёт", 10, "editor.exe")
    draft = win_apps.Window(2, "Черновик", 10, "editor.exe")
    monkeypatch.setattr(window_control, "_OS", "Windows")
    monkeypatch.setattr(win_apps, "list_windows", lambda: [report, draft])
    close = Mock(side_effect=[None, False, True])
    monkeypatch.setattr(win_apps, "close", close)

    command = lambda target: window_control.window_control({"action": "close", "target": target})
    assert "Уточните" in command("editor.exe")
    close.assert_not_called()
    assert "Окно ещё открыто" in command("Отчёт")
    assert "Не удалось закрыть" in command("Отчёт")
    assert "Закрыл" in command("Отчёт")
    assert all(call.args == (report,) for call in close.call_args_list)

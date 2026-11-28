"""Вкладка «Кино»: списки «смотрю / хочу / досмотрел» (core/kino.py)."""
import pytest

from core import kino


@pytest.fixture
def data(tmp_path, monkeypatch):
    marks = [
        {"title": "Игра престолов", "season": 1, "episode": 4, "position": 894, "duration": 3341, "at": 200.0},
        {"title": "Менталист", "season": 1, "episode": 1, "position": 21, "duration": 2708, "at": 100.0},
    ]
    monkeypatch.setattr(kino, "_path", lambda: tmp_path / "kino.json")
    monkeypatch.setattr(kino, "_bookmarks", lambda: list(marks))
    return marks


def test_watching_comes_from_player_bookmarks_freshest_first(data):
    items = kino.shows()
    assert [s.title for s in items] == ["Игра престолов", "Менталист"]
    got = items[0]
    assert got.status == "watching" and got.where() == "Сезон 1 · Серия 4 · осталось 40 мин"
    assert items[1].where() == "Сезон 1 · Серия 1 · только начал"


def test_want_done_remove(data):
    kino.want("Дюна")
    kino.done("Менталист")
    kino.remove("Игра престолов")
    by = {s.title: s.status for s in kino.shows()}
    assert by == {"Дюна": "want", "Менталист": "done"}


def test_removed_comes_back_when_watched_again(data, monkeypatch):
    kino.remove("Игра престолов")
    data[0]["at"] = 9e12                          # снова смотрел позже, чем убрал
    assert "Игра престолов" in [s.title for s in kino.shows()]


def test_voice_tool(data):
    assert "Хочу посмотреть" in kino.kino_tool({"action": "want", "title": "Дюна"})
    text = kino.kino_tool({"action": "list"})
    assert "Смотрите: Игра престолов" in text and "Хотите посмотреть: Дюна" in text
    assert kino.kino_tool({"action": "done"}) == "Какой фильм или сериал?"


def test_kino_page_is_wired():
    import main
    assert "kino" in main._WINDOW_ALLOWED_TOOLS
    win = next(t for t in main.TOOLS if t["name"] == "app_window")
    assert "kino" in win["parameters"]["properties"]["window"]["enum"]


def test_page_renders_hero_list_and_episodes(data, qapp=None):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from ui_kino import KinoDialog
    d = KinoDialog(None, auto_refresh=False)
    d._apply(kino.shows())
    assert d.big.text() == "Игра престолов"
    assert d.eps.count() == 5                     # 1–3 посмотрено, 4 сейчас, 5 следующая
    assert d.list_lay.count() == 2
    d._select("Менталист")
    assert d.big.text() == "Менталист" and d.eps.count() == 2
    d._pick_tab("want")
    assert d.list_lay.count() == 1                # подсказка «пусто»
    app.processEvents()

import sys

import pytest

from actions import open_app as O


@pytest.fixture
def no_apps(monkeypatch):
    opened, rebuilt = [], []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(O.win_apps, "find_windows", lambda key: [])
    monkeypatch.setattr(O.win_apps, "find_app", lambda key, apps=None: None)
    monkeypatch.setattr(O.win_apps, "build_index", lambda force=False: rebuilt.append(force) or [])
    monkeypatch.setattr(O.win_apps, "suggestions", lambda raw: [])
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr(O, "_last_forced_rebuild", float("-inf"))
    return opened, rebuilt


def test_site_without_app_opens_in_browser_without_rebuild(no_apps):
    """Лог 26.11: «открой мой инстаграм» — 45 с пересборки и «что-то пошло не так»."""
    opened, rebuilt = no_apps
    assert "в браузере" in O.open_app({"app_name": "мой Инстаграм"})
    assert opened == ["https://www.instagram.com"]
    assert rebuilt == []


def test_forced_rebuild_at_most_once_per_ten_minutes(no_apps):
    _, rebuilt = no_apps
    O.open_app({"app_name": "неведомая программа"})
    O.open_app({"app_name": "неведомая программа"})
    assert rebuilt == [True]

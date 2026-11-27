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


def test_forced_rebuild_at_most_once_per_ten_minutes(no_apps, monkeypatch):
    monkeypatch.setattr("threading.Thread", lambda target, kwargs, **kw: type("T", (), {"start": lambda self: target(**kwargs)})())
    _, rebuilt = no_apps
    O.open_app({"app_name": "неведомая программа"})
    O.open_app({"app_name": "неведомая программа"})
    assert rebuilt == [True]


def test_site_with_trailing_words_and_no_blocking_rebuild(no_apps, monkeypatch):
    """Журнал 27.11 23:50: «Открой Instagram, говорю» — 45 с пересборки и «ошибка»."""
    opened, rebuilt = no_apps
    assert "в браузере" in O.open_app({"app_name": "instagram говорю"})
    assert opened == ["https://www.instagram.com"]
    started = []
    monkeypatch.setattr("threading.Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})())
    assert "Не нашёл" in O.open_app({"app_name": "неведомая программа"})
    assert started and rebuilt == []                     # пересборка ушла в фон


def test_landing_of_own_project_opens_site(no_apps):
    """28.11 03:05: «открой мне лендинг брона» — 45 с и «ошибка»."""
    opened, rebuilt = no_apps
    assert "в браузере" in O.open_app({"app_name": "мне лендинг брона"})
    assert opened == ["https://brona.metov.store"] and rebuilt == []


def test_stale_index_is_returned_at_once(monkeypatch):
    from core import win_apps as W
    monkeypatch.setattr(W, "_index", [W.App("Telegram")])
    monkeypatch.setattr(W, "_index_at", W.time.monotonic() - 3600)
    started = []
    monkeypatch.setattr(W, "_refresh_in_background", lambda: started.append(1))
    assert [a.name for a in W.build_index()] == ["Telegram"]           # не ждём 45 с
    assert started == [1]

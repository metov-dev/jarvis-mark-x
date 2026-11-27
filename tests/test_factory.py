"""TASK-23: «Джарвис, статус» — сводка Завода."""
from core import factory as F

DATA = {
    "stale": False, "broken": [],
    "fires": [{"project": "Brona", "why": "сайт не отвечает"}],
    "warnings": [{"project": "Babytime", "why": "нет заказов 3 дня"}],
    "today": [{"project": "Brona", "task": "ответить на заявку", "who": "Менеджер", "when": "сегодня"}],
    "payments": [{"project": "Marhamat", "client": "Айгерим", "amount": "15 000 ₸", "due": "2026-11-28"}],
    "doneYesterday": 2,
    "projects": [{"id": "brona", "name": "Brona", "lamp": "red", "why": "сайт не отвечает", "ready": 80, "paused": False},
                 {"id": "babytime", "name": "Babytime", "lamp": "amber", "why": "нет заказов", "ready": 60, "paused": False}],
}


def test_overall_status_mentions_fires_tasks_payments():
    text = F.describe(DATA)
    assert "Горит: Brona — сайт не отвечает" in text
    assert "Babytime" in text and "ответить на заявку" in text and "Айгерим" in text
    assert "Вчера сдано задач: 2" in text


def test_one_project_by_name_any_alphabet():
    assert F.describe(DATA, "брона").startswith("Brona: горит")
    assert "нет" in F.describe(DATA, "Завод42")


def test_no_key_is_explained(monkeypatch):
    monkeypatch.setattr(F, "_config", lambda: ("https://z", ""))
    monkeypatch.setattr(F, "_cache", None)
    assert "Ключи" in F.factory_tool({})


def test_quiet_factory():
    calm = dict(DATA, fires=[], warnings=[], today=[], payments=[], doneYesterday=0)
    assert F.describe(calm) == "Пожаров нет. Задач на сегодня нет."

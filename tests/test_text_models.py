"""Лестница моделей для побочных запросов: квота одной — не смерть функции."""
import pytest

from core import text_models as tm


@pytest.fixture(autouse=True)
def _clean():
    tm.reset()
    yield
    tm.reset()


def test_квота_первой_модели_уводит_на_следующую():
    tried = []

    def fn(model):
        tried.append(model)
        if model == "a":
            raise RuntimeError("429 RESOURCE_EXHAUSTED. You exceeded your current quota")
        return f"ok:{model}"
    assert tm.call(fn, ("a", "b", "c")) == "ok:b"
    assert tried == ["a", "b"]
    tried.clear()
    assert tm.call(fn, ("a", "b", "c")) == "ok:b" and tried == ["b"]   # «a» отдыхает


def test_чужая_ошибка_не_маскируется():
    def fn(model):
        raise ValueError("bad json in prompt")
    with pytest.raises(ValueError):
        tm.call(fn, ("a", "b"))


def test_все_легли_причина_видна():
    def fn(model):
        raise RuntimeError("503 UNAVAILABLE overloaded")
    with pytest.raises(tm.AllModelsDown, match="503"):
        tm.call(fn, ("a", "b"))


def test_модель_из_настроек_первой():
    assert tm.ladder(("a", "b"), first="b")[0] == "b"

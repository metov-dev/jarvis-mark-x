"""Обрывы Gemini в тишине — не ошибка, а лимит памяти не останавливает память (28.11)."""
import pytest


def test_google_side_drops_are_transient():
    import main
    assert main._transient_drop("1008 none. the operation was aborted.")
    assert main._transient_drop("1006 none. abnormal closure [internal]")
    assert not main._transient_drop("1008 api key was reported as leaked")
    assert not main._transient_drop("resource_exhausted")


def test_memory_falls_back_when_all_gemini_models_are_down(monkeypatch):
    from core import text_models
    from memory import conversation as conv

    def down(*a, **k):
        raise text_models.AllModelsDown("429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(text_models, "call", down)
    monkeypatch.setattr("core.onboarding.ensure_gemini_key", lambda interactive=False: "k")
    monkeypatch.setattr(conv, "_fallback_json", lambda p: {"facts": [], "summary": "ok"})
    assert conv._gemini_json("разбери") == {"facts": [], "summary": "ok"}

    monkeypatch.setattr(conv, "_fallback_json", lambda p: None)
    with pytest.raises(text_models.AllModelsDown):
        conv._gemini_json("разбери")             # запасных нет — как раньше: пауза по квоте


def test_fallback_json_extracts_object(monkeypatch):
    from memory import conversation as conv
    import telegram_bot.fallback_llm as fb

    class Chain:
        def __bool__(self):
            return True

        async def complete(self, prompt, temperature=0.7):
            return 'Вот: {"facts": [{"key": "a", "value": "b"}]} — готово'
    monkeypatch.setattr(fb, "FallbackChain", Chain)
    assert conv._fallback_json("x") == {"facts": [{"key": "a", "value": "b"}]}

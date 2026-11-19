"""Журнал гейта микрофона: одна строка на смену причины, а не каждые 5 с."""
import logging
import types

import main as jarvis_main


def test_одна_строка_на_смену_причины(caplog, monkeypatch):
    monkeypatch.setattr(jarvis_main, "_GATE_REPORT_SEC", 0.0)
    obj = types.SimpleNamespace()
    note = lambda r: jarvis_main.Jarvis._note_gate(obj, r)  # noqa: E731
    with caplog.at_level(logging.INFO, logger=jarvis_main.logger.name):
        for _ in range(5):
            note("жду слово «Джарвис»")
        note("Джарвис говорит сам")
        note("жду слово «Джарвис»")
    lines = [r.getMessage() for r in caplog.records if "Микрофон гейт" in r.getMessage()]
    assert len(lines) == 3, lines

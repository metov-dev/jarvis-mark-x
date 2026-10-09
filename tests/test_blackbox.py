"""Чёрный ящик: события в файл, F9 сохраняет звук, замер не уходит в минус."""
import json
import os
import time
import wave

from core.blackbox import BlackBox
from core.latency import LatencyTracker


def test_events_are_written_as_json_lines(tmp_path):
    bb = BlackBox(tmp_path)
    bb.event("wake", by="name")
    bb.event("turn", heard="Джарвис, привет", answer="Слушаю, сэр.")
    files = list(tmp_path.glob("events-*.jsonl"))
    assert len(files) == 1
    rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["wake", "turn"]
    assert rows[1]["heard"] == "Джарвис, привет"


def test_report_miss_saves_last_seconds_of_audio_and_events(tmp_path):
    bb = BlackBox(tmp_path, rate=16000, chunk_bytes=2048)
    for _ in range(50):
        bb.feed(b"\x01\x00" * 1024)
    bb.event("not_to_me", heard="Červese.")
    wav = bb.report_miss()
    assert wav is not None and wav.exists()
    with wave.open(str(wav)) as w:
        assert w.getframerate() == 16000 and w.getnframes() == 50 * 1024
    info = json.loads(wav.with_suffix(".json").read_text(encoding="utf-8"))
    assert info["events"][-1]["heard"] == "Červese."


def test_ring_keeps_only_last_seconds(tmp_path):
    bb = BlackBox(tmp_path, rate=16000, chunk_bytes=2048)
    for _ in range(10_000):
        bb.feed(b"\x00\x00" * 1024)
    seconds = sum(len(p) for _, p in bb._ring) / (2 * 16000)
    assert 19 <= seconds <= 21


def test_old_files_are_removed(tmp_path):
    old = tmp_path / "miss-old.wav"
    old.write_bytes(b"x")
    past = time.time() - 30 * 86400
    os.utime(old, (past, past))
    BlackBox(tmp_path)
    assert not old.exists()


def test_noise_after_answer_does_not_move_turn_start(monkeypatch):
    clock = iter([0.0, 0.5, 1.0, 1.2, 1.4, 1.6])
    monkeypatch.setattr("core.latency.time.perf_counter", lambda: next(clock))
    lt = LatencyTracker(enabled=True)
    lt.mark_voice_frame()          # 0.0 — речь
    lt.mark_voice_frame()          # 0.5 — конец речи
    lt.mark_answer_audio()         # 1.0 — модель отвечает
    lt.mark_voice_frame()          # 1.2 — шум комнаты
    lt.mark_voice_frame()          # 1.4 — шум комнаты
    lt.mark_playback()             # 1.6 — звук пошёл
    lt.mark_turn_complete()
    assert lt.last_turn["отвечает"] == 500
    assert lt.last_turn["звучит"] == 1100

"""Tests for intelligent audio input device selection (headphones priority)."""

from types import SimpleNamespace

import main as jarvis_main


def test_pick_input_device_prioritizes_headphones(monkeypatch):
    mock_devices = [
        {"name": "Realtek(R) Audio", "max_input_channels": 4, "hostapi": 0},
        {"name": "AI Noise-cancelling Input (ASUS)", "max_input_channels": 2, "hostapi": 0},
        {"name": "AirPods Pro Hands-Free AG Audio", "max_input_channels": 1, "hostapi": 0},
    ]

    monkeypatch.setattr(jarvis_main.sd, "query_devices", lambda: mock_devices)
    monkeypatch.setattr(jarvis_main, "_device_is_silent", lambda idx, **kw: False)
    monkeypatch.setattr(jarvis_main.sd, "default", SimpleNamespace(device=(-1, -1)))
    monkeypatch.setenv("MIC_DEVICE", "")

    picked = jarvis_main._pick_input_device()
    # Should pick AirPods (index 2) over laptop AI Noise-cancelling (index 1)
    assert picked == 2


def test_pick_input_device_fallback_to_laptop_noise_cancelling(monkeypatch):
    mock_devices = [
        {"name": "Realtek(R) Audio", "max_input_channels": 4, "hostapi": 0},
        {"name": "AI Noise-cancelling Input (ASUS)", "max_input_channels": 2, "hostapi": 0},
    ]

    monkeypatch.setattr(jarvis_main.sd, "query_devices", lambda: mock_devices)
    monkeypatch.setattr(jarvis_main, "_device_is_silent", lambda idx, **kw: False)
    monkeypatch.setattr(jarvis_main.sd, "default", SimpleNamespace(device=(-1, -1)))
    monkeypatch.setenv("MIC_DEVICE", "")

    picked = jarvis_main._pick_input_device()
    # Should pick AI Noise-cancelling (index 1) when no headphones are connected
    assert picked == 1


def test_pick_input_device_manual_override(monkeypatch):
    mock_devices = [
        {"name": "Realtek(R) Audio", "max_input_channels": 4, "hostapi": 0},
        {"name": "AI Noise-cancelling Input (ASUS)", "max_input_channels": 2, "hostapi": 0},
        {"name": "AirPods Pro Hands-Free AG Audio", "max_input_channels": 1, "hostapi": 0},
    ]

    monkeypatch.setattr(jarvis_main.sd, "query_devices", lambda: mock_devices)
    monkeypatch.setattr(jarvis_main, "_device_is_silent", lambda idx, **kw: False)
    monkeypatch.setenv("MIC_DEVICE", "realtek")

    picked = jarvis_main._pick_input_device()
    assert picked == 0


def test_webcam_mic_is_not_mistaken_for_headset(monkeypatch):
    mock_devices = [
        {"name": "Microphone (USB Camera)", "max_input_channels": 1, "hostapi": 0},
        {"name": "AI Noise-cancelling Input (ASUS)", "max_input_channels": 2, "hostapi": 0},
    ]
    monkeypatch.setattr(jarvis_main.sd, "query_devices", lambda: mock_devices)
    monkeypatch.setattr(jarvis_main, "_device_is_silent", lambda idx, **kw: False)
    monkeypatch.setattr(jarvis_main.sd, "default", SimpleNamespace(device=(-1, -1)))
    monkeypatch.setenv("MIC_DEVICE", "")

    assert jarvis_main._pick_input_device() == 1

def test_pick_input_device_prefers_windows_default_to_virtual_noise_cancelling(monkeypatch):
    devices = [
        {"name": "Microphone Array (Realtek)", "max_input_channels": 4, "hostapi": 0},
        {"name": "AI Noise-cancelling Input (ASUS)", "max_input_channels": 2, "hostapi": 0},
    ]
    monkeypatch.setattr(jarvis_main.sd, "query_devices", lambda: devices)
    monkeypatch.setattr(jarvis_main.sd, "default", SimpleNamespace(device=(0, -1)))
    monkeypatch.setattr(jarvis_main, "_device_is_silent", lambda idx, **kw: False)
    monkeypatch.setenv("MIC_DEVICE", "")

    assert jarvis_main._pick_input_device() == 0

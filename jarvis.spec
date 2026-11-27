# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification file for building standalone JARVIS Mark X executable.

КРИТИЧЕСКИ ВАЖНО: Никогда не паковать реальные секреты (api_keys.json, userbot.session,
jarvis_memory.db). Пакуются только безопасные шаблоны и ассеты.
"""

import sys
from pathlib import Path

block_cipher = None

BASE_DIR = Path(SPECPATH)

# Только безопасные шаблоны и ассеты (БЕЗ личных данных, токенов и сессий)
datas = []

safe_files = [
    ("core/prompt.txt", "core"),
    ("config/modes.json", "config"),
    ("config/obsidian.json", "config"),
    ("config/api_keys.example.json", "config"),
    ("face.png", "."),
    ("app.ico", "."),
]

for src, dst in safe_files:
    full = BASE_DIR / src
    if full.exists():
        datas.append((str(full), dst))

if (BASE_DIR / "assets").exists():
    datas.append((str(BASE_DIR / "assets"), "assets"))

# Словарь слова «Джарвис» (~45 МБ): кладёт scripts/build_exe.py. Нет — Джарвис
# работает, но имя ищет через Gemini (см. core/wake_vosk.py).
if (BASE_DIR / "models" / "vosk-small-ru").exists():
    datas.append((str(BASE_DIR / "models" / "vosk-small-ru"), "models/vosk-small-ru"))
# Отпечатки голоса владельца (core/voice_id.py); нет — докачивается из окна «Обо мне».
if (BASE_DIR / "models" / "vosk-spk").exists():
    datas.append((str(BASE_DIR / "models" / "vosk-spk"), "models/vosk-spk"))
if (BASE_DIR / "models" / "voice-id").exists():
    datas.append((str(BASE_DIR / "models" / "voice-id"), "models/voice-id"))
# Слово «Джарвис» без ключей (core/wake_kws.py, русская модель 28 МБ); нет — докачается при запуске.
if (BASE_DIR / "models" / "kws-jarvis-ru").exists():
    datas.append((str(BASE_DIR / "models" / "kws-jarvis-ru"), "models/kws-jarvis-ru"))

if (BASE_DIR / "telegram_bot" / "miniapp").exists():
    datas.append((str(BASE_DIR / "telegram_bot" / "miniapp"), "telegram_bot/miniapp"))

hidden_imports = [
    "google.genai",
    "google.genai.types",
    "sounddevice",
    "_sounddevice_data",
    "edge_tts",
    "pydub",
    "psutil",
    "rapidfuzz",
    "feedparser",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "core",
    "core.paths",
    "core.emotion_analyzer",
    "core.proactive_engine",
    "core.initiative_engine",
    "core.team_collaboration",
    "core.user_profile",
    "core.latency",
    "core.wakeword",
    "core.tray",
    "core.ducking_controller",
    "core.audio_capture",
    "core.aec_pipeline",
    "core.wake_detector",
    "core.voice_trigger_engine",
    "ui_setup",
    "telegram_bot.tts_edge",
    "telegram_bot.tts_fish",
    "telegram_bot.voice",
    "actions.vision",
    "actions.telegram_sender",
    "actions.obsidian",
    "actions.open_app",
    "actions.web_search",
    "actions.window_control",
    "actions.computer_settings",
    "actions.weather",
    "actions.music_player",
    "actions.spotify_controller",
    "actions.movie_player",
    "actions.video_player",
    "actions.spotify_premium",
    "core.browser_cdp",
    "core.browser_panel",
    "ui_browser",
    "ui_island",
    "ui_icons",
    "core.selftest",
    "core.break_reminder",
    "core.tg_call",
    "core.wake_calibrate",
    "core.clock",
    "core.location",
    "core.eyes",
    "core.verify",
    "core.quick",
    "core.macros",
    "core.macro_packs",
    "ui_macros",
    "ui_kit",
    "ui_keys",
    "core.keys",
    "core.contacts",
    "core.about_me",
    "core.briefing",
    "core.voice_id",
    "core.study",
    "ui_study",
    "core.help",
    "ui_welcome",
    "ui_about",
    "ui_contacts",
    "qrcode",
    "telegram_bot.pc_userbot",
    "telegram_bot.pc_server",
    "websockets.sync.client",
    "websockets.asyncio.client",
    "actions.sleep_timer",
    "actions.file_controller",
    "actions.morning_briefing",
    "actions.calendar",
    "core.storage",
    "core.onboarding",
    "core.hotkey_manager",
    "core.headless_ui",
    "mss",
    "imageio_ffmpeg",
    "pycaw",
    "comtypes",
    "pyaudiowpatch",
    "numpy",
    "onnxruntime",
]

binaries = []
from PyInstaller.utils.hooks import collect_all

# Каждый пакет — отдельно: раньше первый отсутствующий (openwakeword нет в
# requirements) обрывал цикл, и pycaw/comtypes в сборку не попадали —
# приглушение музыки и замер колонок в .exe молча не работали.
for pkg in ["imageio_ffmpeg", "openwakeword", "pycaw", "comtypes", "pyaudiowpatch", "mss", "vosk", "screen_brightness_control", "winrt", "ddgs", "telethon", "pytgcalls", "ntgcalls", "tzdata", "sherpa_onnx", "pvporcupine"]:
    print(f"[JARVIS build] collecting {pkg}", flush=True)
    try:
        if pkg == "pytgcalls":
            # Importing pytgcalls runs pytgcalls.sync, which constructs an
            # asyncio event loop while PyInstaller is in its isolated hook
            # process. On Windows its socketpair fallback can block forever.
            # The package contains Python files only, so enumerate them by
            # path and let Analysis parse them without executing package code.
            package_dir = Path(sys.prefix) / "Lib" / "site-packages" / pkg
            for source in package_dir.rglob("*.py"):
                parts = source.relative_to(package_dir.parent).with_suffix("").parts
                if parts[-1] == "__init__":
                    parts = parts[:-1]
                if parts:
                    hidden_imports.append(".".join(parts))
            continue
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        # collect_all("comtypes") и некоторые другие пакеты включают свои
        # тестовые модули в hidden imports. Это не нужно в приложении и
        # заставляет PyInstaller анализировать десятки неиспользуемых модулей.
        pkg_hidden = [
            module for module in pkg_hidden
            if not any(part.casefold() in {"test", "tests"} for part in module.split("."))
        ]
        if pkg == "tzdata":
            # ZoneInfo reads timezone files as package resources; it does not
            # import hundreds of generated zoneinfo modules at runtime.
            pkg_hidden = [m for m in pkg_hidden if m in {"tzdata", "tzdata.zoneinfo"}]
        elif pkg == "sherpa_onnx":
            # CLI entry points are not used by Jarvis; its speech features
            # import the public package API directly.
            pkg_hidden = [m for m in pkg_hidden if m not in {"sherpa_onnx.__main__", "sherpa_onnx.cli"}]
    except Exception as e:
        print(f"Hook collection notice ({pkg}): {e}")
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hidden_imports += pkg_hidden

from PyInstaller.building import build_main as _build_main
_find_binary_dependencies = _build_main.find_binary_dependencies

def _find_binary_dependencies_without_pytgcalls_import(binaries, import_packages, symlink_suppression_patterns):
    # PyTgCalls creates an asyncio loop at import time. PyInstaller imports
    # collected packages in a helper process only to discover added DLL paths;
    # that import blocks on Windows socketpair. Its native dependency package
    # (ntgcalls) remains in the scan, so skip only the Python-only wrapper.
    safe_packages = [name for name in import_packages
                     if name != "pytgcalls" and not name.startswith("pytgcalls.")]
    return _find_binary_dependencies(binaries, safe_packages, symlink_suppression_patterns)

_build_main.find_binary_dependencies = _find_binary_dependencies_without_pytgcalls_import

a = Analysis(
    ['main.py'],
    pathex=[str(BASE_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'torch', 'torchvision', 'transformers', 'pytest',
              'PyQt6.QtNetwork'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='JARVIS',
    icon=str(BASE_DIR / 'app.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='JARVIS',
)

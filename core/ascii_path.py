"""Путь к папке модели только из ASCII.

Vosk (Kaldi) на Windows не открывает модель, если в пути есть кириллица:
«Failed to create a model». Так бывает и в папке проекта («Проекты»), и у
пользователей с русским именем профиля. Для такой папки делаем ASCII-ссылку
(junction, без прав администратора), а если не вышло — копию.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger("JARVIS")


def _ready(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def ascii_model_dir(model_dir: Path, base: Path | None = None) -> Path:
    """Вернуть путь к модели без не-ASCII символов (или исходный, если это невозможно)."""
    model_dir = Path(model_dir)
    if str(model_dir).isascii():
        return model_dir
    base = base or Path(os.environ.get("PUBLIC") or "C:/Users/Public") / "JARVIS-models"
    link = base / model_dir.name
    if _ready(link):
        return link
    try:
        base.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():      # битая ссылка (проект переехал)
            try:
                os.rmdir(link)
            except OSError:
                shutil.rmtree(link, ignore_errors=True)
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(model_dir)],
                           capture_output=True, check=False, timeout=15)
        if not _ready(link):
            shutil.copytree(model_dir, link, dirs_exist_ok=True)
        if _ready(link) and str(link).isascii():
            logger.info("Модель %s: путь с кириллицей, использую %s", model_dir.name, link)
            return link
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ASCII-путь для модели %s не создан: %s", model_dir.name, exc)
    return model_dir

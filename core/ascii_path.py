"""Путь к папке модели только из ASCII.

Vosk (Kaldi) на Windows не открывает модель, если в пути есть кириллица:
«Failed to create a model». Так бывает и в папке проекта («Проекты»), и у
пользователей с русским именем профиля. Для такой папки делаем ASCII-ссылку
(junction, без прав администратора).
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger("JARVIS")


def _ready(path: Path) -> bool:
    try:
        return path.is_dir() and any(path.iterdir())
    except OSError:
        return False


def _points_to(link: Path, target: Path) -> bool:
    """Ссылка действительно ведёт на нашу модель (а не на чужую папку в общем каталоге)."""
    try:
        return _ready(link) and os.path.samefile(link, target)
    except OSError:
        return False


def ascii_model_dir(model_dir: Path, base: Path | None = None) -> Path:
    """Вернуть путь к модели без не-ASCII символов (или исходный, если это невозможно).

    Ссылка живёт в общем каталоге, куда могут писать и другие пользователи ПК,
    поэтому берём только ссылку, которая ведёт ровно на нашу папку; чужую или
    битую заменяем, а если заменить нельзя — отказываемся (модель не грузим).
    """
    model_dir = Path(model_dir)
    if str(model_dir).isascii():
        return model_dir
    base = base or Path(os.environ.get("PUBLIC") or "C:/Users/Public") / "JARVIS-models"
    link = base / model_dir.name
    if _points_to(link, model_dir):
        return link
    try:
        base.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(link):
            os.rmdir(link)            # junction/пустую папку; чужую непустую — не удаляем
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(model_dir)],
                           capture_output=True, check=False, timeout=15)
        if _points_to(link, model_dir) and str(link).isascii():
            logger.info("Модель %s: путь с кириллицей, использую %s", model_dir.name, link)
            return link
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ASCII-путь для модели %s не создан: %s", model_dir.name, exc)
    return model_dir

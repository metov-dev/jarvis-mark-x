"""
Действие: работа с файлами и папками
"""

import os
import shutil
from pathlib import Path


_SHORTCUTS = {
    "desktop": os.path.join(os.path.expanduser("~"), "Desktop"),
    "рабочий стол": os.path.join(os.path.expanduser("~"), "Desktop"),
    "downloads": os.path.join(os.path.expanduser("~"), "Downloads"),
    "загрузки": os.path.join(os.path.expanduser("~"), "Downloads"),
    "documents": os.path.join(os.path.expanduser("~"), "Documents"),
    "документы": os.path.join(os.path.expanduser("~"), "Documents"),
    "home": os.path.expanduser("~"),
    "домашняя": os.path.expanduser("~"),
}


def _resolve(path: str) -> str:
    return _SHORTCUTS.get(path.lower().strip(), path)


def _is_protected(p: Path) -> bool:
    """Папки, которые голосом не удаляются никогда: дом, рабочий стол,
    загрузки, документы, их родители и корни дисков."""
    try:
        target = p.expanduser().resolve()
    except OSError:
        return True
    home = Path(os.path.expanduser("~")).resolve()
    protected = {home, *(Path(v).resolve() for v in _SHORTCUTS.values())}
    if target in protected or target == Path(target.anchor):
        return True
    return target in home.parents


_SENSITIVE_NAMES = {
    "api_keys.json", "token.json", "credentials.json", "unlock_secret.txt",
    ".env", "id_rsa", "id_ed25519", "userbot.session", "userbot.session-journal",
    "jarvis_memory.db", "settings.json", "web.config",
}
_SENSITIVE_EXTS = {".env", ".session", ".session-journal", ".pem", ".key", ".pfx", ".kdbx"}
_SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", "system32"}


def _is_sensitive(p: Path) -> bool:
    """Защита от утечки секретов и системных конфигов через ИИ-ассистента."""
    try:
        resolved = p.expanduser().resolve()
    except OSError:
        return True
    name_low = resolved.name.lower()
    if name_low in _SENSITIVE_NAMES or resolved.suffix.lower() in _SENSITIVE_EXTS:
        return True
    parts_low = {part.lower() for part in resolved.parts}
    if parts_low.intersection(_SENSITIVE_DIRS):
        return True
    if "config" in parts_low and any(k in name_low for k in ("key", "secret", "token", "cred", "pass", "session", "json")):
        return True
    return False


def _transfer_blocked(path: str, dest: str) -> bool:
    """Копирование/перенос: защищён источник, папка-приёмник, итоговый путь или любой файл внутри папки."""
    src, target = Path(path), Path(dest)
    final = target / src.name if target.is_dir() else target
    if _is_sensitive(src) or _is_sensitive(target) or _is_sensitive(final):
        return True
    if src.is_dir():
        try:
            return any(_is_sensitive(item) for item in src.rglob("*"))
        except OSError:
            return True
    return False


def deletion_target(raw_path: str) -> Path:
    """Resolve the same full path for confirmation and deletion."""
    import stat

    if not raw_path:
        raise ValueError("не указано, что именно удалить")
    p = Path(_resolve(raw_path)).expanduser()
    if _is_protected(p) or _is_sensitive(p):
        raise ValueError(f"системный или защищённый файл: {p}")
    info = p.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f"путь указывает на ссылку или точку подключения: {p}")
    return p.resolve(strict=True)


def _to_recycle_bin(p: Path) -> None:
    """Windows: в Корзину (можно вернуть). Иначе — обычное удаление."""
    import sys
    if sys.platform != "win32":
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT = 3, 0x40, 0x10, 0x4
    op = SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=str(p) + "\0\0",
                         fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError(f"не удалось переместить в Корзину (код {rc})")


def file_controller(parameters: dict, player=None) -> str:
    action = parameters.get("action", "list").lower()
    raw_path = str(parameters.get("path") or "").strip()
    path = _resolve(raw_path or os.path.expanduser("~"))
    dest = _resolve(parameters.get("destination", ""))
    name = parameters.get("name", "")
    content = parameters.get("content", "")
    new_name = parameters.get("new_name", "")

    try:
        if action == "list":
            p = Path(path)
            if not p.exists():
                return f"Папка не найдена: {path}"
            items = list(p.iterdir())
            dirs = [f"📁 {i.name}" for i in items if i.is_dir()]
            files = [f"📄 {i.name}" for i in items if i.is_file()]
            all_items = dirs + files
            if not all_items:
                return f"Папка пуста: {path}"
            preview = all_items[:12]
            extra = f" (+{len(all_items)-12} ещё)" if len(all_items) > 12 else ""
            if player:
                player.write_log(f"SYS: Файлы в {path}")
            return f"Содержимое {path}: {', '.join(preview)}{extra}."

        elif action == "read":
            p = Path(path)
            if _is_sensitive(p):
                return f"Доступ к защищённому файлу ограничен: {p.name}"
            if not p.exists():
                return f"Файл не найден: {path}"
            text = p.read_text(encoding="utf-8", errors="replace")
            preview = text[:600]
            if player:
                player.write_log(f"FILE: Читаю {p.name}")
            return f"Содержимое {p.name}: {preview}{'...' if len(text) > 600 else ''}"

        elif action == "create_file":
            p = Path(path)
            if _is_sensitive(p):
                return "Создание файлов с таким именем запрещено политикой безопасности."
            if p.exists():
                return f"Файл уже существует, не перезаписываю: {p}."
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            if player:
                player.write_log(f"FILE: Создан {p.name}")
            return f"Файл создан: {p}."

        elif action == "create_folder":
            p = Path(path)
            p.mkdir(parents=True, exist_ok=True)
            if player:
                player.write_log(f"FILE: Папка создана {p.name}")
            return f"Папка создана: {p}."

        elif action == "delete":
            # Раньше путь по умолчанию был домашней папкой, а «desktop» —
            # всем рабочим столом: вызов без пути делал rmtree(~).
            try:
                p = deletion_target(raw_path)
            except ValueError as exc:
                return f"Не удаляю: {exc}."
            except FileNotFoundError:
                return f"Не найдено: {raw_path}"
            if expected := parameters.get("_file_identity"):
                info = p.stat()
                if expected != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size):
                    return "Не удалось удалить: объект изменился после подтверждения. Повторите команду."
            _to_recycle_bin(p)
            if player:
                player.write_log(f"FILE: В корзину {p.name}")
            import sys
            return f"Удалено (в Корзину): {p.name}." if sys.platform == "win32" else f"Удалено: {p.name}."

        elif action == "move":
            if _transfer_blocked(path, dest):
                return "Операция с защищёнными файлами запрещена."
            shutil.move(path, dest)
            if player:
                player.write_log(f"FILE: Перемещено → {dest}")
            return f"Перемещено в {dest}."

        elif action == "copy":
            if _transfer_blocked(path, dest):
                return "Операция с защищёнными файлами запрещена."
            src = Path(path)
            if src.is_dir():
                shutil.copytree(path, dest)
            else:
                shutil.copy2(path, dest)
            if player:
                player.write_log(f"FILE: Скопировано → {dest}")
            return f"Скопировано в {dest}."

        elif action == "rename":
            p = Path(path)
            if _is_sensitive(p) or _is_sensitive(p.parent / new_name):
                return "Операция с защищёнными файлами запрещена."
            new_p = p.parent / new_name
            p.rename(new_p)
            if player:
                player.write_log(f"FILE: Переименовано → {new_name}")
            return f"Переименовано в {new_name}."

        elif action == "find":
            base = Path(path)
            results = list(base.rglob(f"*{name}*"))[:10]
            if not results:
                return f"Файлы с именем «{name}» не найдены."
            found = [str(r) for r in results]
            return "Найдено: " + ", ".join(found[:5]) + ("..." if len(results) > 5 else ".")

        elif action == "disk_usage":
            # Используем домашнюю директорию — корректно на всех OS,
            # включая Windows (где "/" даёт неправильные данные).
            target = path if path and Path(path).exists() else os.path.expanduser("~")
            total, used, free = shutil.disk_usage(target)
            gb = 1024**3
            return (f"Диск: всего {total/gb:.1f} ГБ, "
                    f"использовано {used/gb:.1f} ГБ, "
                    f"свободно {free/gb:.1f} ГБ.")

        else:
            return f"Неизвестное действие: {action}"

    except Exception as e:
        return f"Ошибка файловой операции: {e}"

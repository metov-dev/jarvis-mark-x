"""JARVIS Mark X — Единая система управления путями и конфигурацией.

Корректно работает как в режиме разработки (исходный код), так и в собранном
бинарнике PyInstaller (frozen .exe с _internal и _MEIPASS), а также гарантирует
запись пользовательских настроек в %APPDATA%/JARVIS без требования прав Администратора.
"""
import json
import base64
import ctypes
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("jarvis-paths")


def get_base_dir() -> Path:
    """Возвращает базовую директорию ресурсов приложения (dev или _MEIPASS в .exe)."""
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(sys.executable).parent / "_internal"
    return Path(__file__).resolve().parent.parent


def get_app_dir() -> Path:
    """Директория с исполняемым файлом (куда установлен .exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def get_user_data_dir() -> Path:
    """Возвращает гарантированно доступную для записи папку пользователя."""
    if sys.platform == "win32":
        base = Path(os.getenv("APPDATA", str(Path.home())))
    else:
        base = Path.home() / ".config"
    user_dir = base / "JARVIS"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_data_root() -> Path:
    """Корень изменяемых данных (профиль, память, календарь, кэши).

    Из исходников — папка проекта, как и раньше: данные остаются на месте.
    В собранном .exe — %APPDATA%/JARVIS. Раньше данные писались в папку
    программы: в Program Files запись запрещена, и профиль, память и
    календарь молча не сохранялись, а переустановка стирала их.
    """
    if getattr(sys, "frozen", False):
        root = get_user_data_dir()
        for sub in ("config", "memory"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        return root
    return Path(__file__).resolve().parent.parent


def get_config_path(filename: str = "api_keys.json", for_writing: bool = False) -> Path:
    """Возвращает путь к файлу конфигурации.

    Если for_writing=True — возвращает путь в папке пользователя (%APPDATA%/JARVIS).
    Если for_writing=False — проверяет сначала %APPDATA%, затем встроенный каталог config/.
    """
    user_path = get_user_data_dir() / filename
    if for_writing:
        return user_path

    # Чтение: сначала проверяем пользовательские настройки
    if user_path.exists():
        return user_path

    # Затем проверяем встроенный конфиг в корне или _internal
    base_config = get_base_dir() / "config" / filename
    if base_config.exists():
        return base_config

    app_config = get_app_dir() / "config" / filename
    if app_config.exists():
        return app_config

    return user_path


def get_prompt_path() -> Path:
    """Возвращает путь к системному промпту Джарвиса (core/prompt.txt)."""
    # 1. В ресурсах сборки / репозитория
    p = get_base_dir() / "core" / "prompt.txt"
    if p.exists():
        return p
    # 2. Рядом с исполняемым файлом
    p2 = get_app_dir() / "core" / "prompt.txt"
    if p2.exists():
        return p2
    return p


def load_api_keys() -> dict:
    """Загружает API ключи и настройки из всех возможных источников."""
    # 1. Сначала из %APPDATA%
    user_cfg = get_user_data_dir() / "api_keys.json"
    data = {}
    if user_cfg.exists():
        try:
            data.update(_read_api_keys_file(user_cfg))
        except Exception as e:
            logger.warning("Failed to load API keys from %s: %s", user_cfg, e)    

    # 2. Если в APPDATA чего-то нет, подмешиваем из локального config/api_keys.json
    local_cfg = get_app_dir() / "config" / "api_keys.json"
    if local_cfg.exists():
        try:
            local_data = _read_api_keys_file(local_cfg)
            for k, v in local_data.items():
                if k not in data or not data[k]:
                    data[k] = v
        except Exception as e:
            logger.warning("Failed to load API keys from %s: %s", local_cfg, e)

    return data


def save_api_keys(data: dict) -> bool:
    """Сохраняет API ключи в безопасную пользовательскую директорию %APPDATA%."""
    saved = False
    targets = [get_user_data_dir() / "api_keys.json"]

    # В режиме разработки (не скомпилированный .exe) также обновляем локальный config если он существует
    if not getattr(sys, "frozen", False):
        local_dev = get_app_dir() / "config" / "api_keys.json"
        if local_dev.exists():
            targets.append(local_dev)

    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = {}
            if target.exists():
                try:
                    existing = _read_api_keys_file(target)
                except Exception:
                    pass
            existing.update(data)
            _write_api_keys_file(target, existing)
            saved = True
        except Exception as e:
            logger.debug("Failed to write to %s: %s", target, e)
    return saved

class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

def _dpapi_crypt(data: bytes, encrypt: bool) -> bytes:
    blob_in = _DATA_BLOB(len(data), (ctypes.c_ubyte * len(data)).from_buffer_copy(data))
    blob_out = _DATA_BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise ctypes.WinError()
        res = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return res
def _get_fernet():
    from cryptography.fernet import Fernet
    key_path = get_user_data_dir() / ".key"
    if key_path.exists():
        key = key_path.read_bytes().strip()
    else:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        if os.name != "nt":
            os.chmod(key_path, 0o600)
    return Fernet(key)

def _read_api_keys_file(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            enc_type = parsed.get("_enc")
            if enc_type == "dpapi" and sys.platform == "win32":
                decrypted = _dpapi_crypt(base64.b64decode(parsed["data"]), encrypt=False)
                return json.loads(decrypted.decode("utf-8"))
            if enc_type == "fernet":
                decrypted = _get_fernet().decrypt(parsed["data"].encode("ascii"))
                return json.loads(decrypted.decode("utf-8"))
            return parsed
    except json.JSONDecodeError:
        pass
    return {}


def _write_api_keys_file(path: Path, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    if sys.platform == "win32":
        encrypted = _dpapi_crypt(payload, encrypt=True)
        out = {"_enc": "dpapi", "data": base64.b64encode(encrypted).decode("ascii")}
    else:
        token = _get_fernet().encrypt(payload).decode("ascii")
        out = {"_enc": "fernet", "data": token}
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    if os.name != "nt":
        os.chmod(path, 0o600)
        

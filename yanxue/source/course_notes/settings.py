"""Local application settings; credentials are session-only or Windows DPAPI protected."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from .core_types import SettingsError

SESSION_KEYS: dict[str, str] = {}


def settings_path(course: Path) -> Path:
    return course.resolve().parent.parent / "local-settings.json"


def load_settings(course: Path) -> dict:
    path = settings_path(course)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _dpapi(value: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise SettingsError("此系统不支持 Windows 加密保存，请使用仅本次会话。")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    source = ctypes.create_string_buffer(value)
    incoming = Blob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
        raise SettingsError("Windows 无法加密或解密密钥，请重新输入。")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        ctypes.windll.kernel32.LocalFree(outgoing.data)


def api_key(course: Path, settings: dict | None = None) -> str:
    identity = str(settings_path(course))
    if identity in SESSION_KEYS:
        return SESSION_KEYS[identity]
    settings = settings if settings is not None else load_settings(course)
    encrypted = settings.get("encrypted_api_key", "")
    if encrypted:
        try:
            return _dpapi(base64.b64decode(encrypted), decrypt=True).decode("utf-8")
        except Exception:
            return ""
    return os.environ.get("OPENAI_API_KEY", "")


def effective_config(course: Path, config: dict) -> dict:
    settings = load_settings(course)
    result = {**config, "models": {**config.get("models", {}), **settings.get("models", {})},
              "automation": {**config.get("automation", {})}}
    if settings.get("downloader_root"):
        result["automation"]["downloader_root"] = settings["downloader_root"]
    result["models"]["base_url"] = result["models"].get("base_url") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    result["_api_key"] = api_key(course, settings)
    return result


def save_settings(course: Path, values: dict) -> None:
    settings = load_settings(course)
    root = str(values.get("downloader_root", "")).strip()
    if root:
        path = Path(root).resolve()
        if (path / "app.py").is_file():
            path = path.parent
        if not (path / "pku_recorder_app" / "app.py").is_file():
            raise SettingsError("所选目录中找不到 pku_recorder_app/app.py。")
        settings["downloader_root"] = str(path)
    else:
        settings.pop("downloader_root", None)
    models = {}
    for name in ("writer", "reviewer", "transcriber", "base_url"):
        value = str(values.get(name, "")).strip()
        if value:
            models[name] = value
    if models.get("base_url"):
        parsed = urlparse(models["base_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise SettingsError("服务地址需为 http(s) URL，且不能包含账号或密码。")
    settings["models"] = models
    key = str(values.get("api_key", "")).strip()
    identity = str(settings_path(course))
    if values.get("clear_key"):
        settings.pop("encrypted_api_key", None)
        SESSION_KEYS[identity] = ""
    elif key:
        if values.get("remember_key"):
            settings["encrypted_api_key"] = base64.b64encode(_dpapi(key.encode("utf-8"))).decode("ascii")
        else:
            settings.pop("encrypted_api_key", None)
        SESSION_KEYS[identity] = key
    path = settings_path(course)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

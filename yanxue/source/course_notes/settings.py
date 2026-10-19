"""Local application settings; credentials are session-only or Windows DPAPI protected."""
from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from .core_types import SettingsError
from .records import read_record, backup_record, ERROR

SESSION_KEYS: dict[str, str] = {}


def settings_path(course: Path) -> Path:
    return course.resolve().parent.parent / "local-settings.json"


def load_settings(course: Path) -> dict:
    path = settings_path(course)
    settings = read_record(path)
    if not isinstance(settings.get("models", {}), dict) or any(not isinstance(v, str) for v in settings.get("models", {}).values()) or any(not isinstance(settings.get(key, ""), str) for key in ["downloader_root", "current_course", "encrypted_api_key"]):
        return {ERROR: "本机连接配置字段格式错误，请在连接设置中重新保存。原文件未改动。"}
    return settings


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
    settings = settings if settings is not None else load_settings(course)
    if settings.get(ERROR):
        return ""
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


def course_options_record(course: Path) -> dict:
    options = read_record(course / "course-options.json")
    if not isinstance(options.get("terms", []), list) or any(not isinstance(term, str) for term in options.get("terms", [])) or not isinstance(options.get("profile", "general"), str) or options.get("profile", "general") not in {"general", "stem", "economics", "humanities", "law", "language"}:
        return {ERROR: "课程模板配置损坏，请重新保存这门课的模板与术语。"}
    return options


def effective_config(course: Path, config: dict) -> dict:
    if any(not isinstance(config.get(key, {}), dict) for key in ["course", "models", "automation"]):
        raise SettingsError("course.toml 中 course、models、automation 应为配置分组。")
    course_values = config.get("course", {})
    if not isinstance(course_values.get("title", ""), str) or not isinstance(course_values.get("terms", []), list) or any(not isinstance(term, str) for term in course_values.get("terms", [])) or any(not isinstance(value, str) for value in config.get("models", {}).values()):
        raise SettingsError("course.toml 中课程名称、术语或模型字段格式错误。")
    settings = load_settings(course)
    result = {**config, "models": {**config.get("models", {}), **settings.get("models", {})},
              "automation": {**config.get("automation", {})}}
    if settings.get("downloader_root"):
        result["automation"]["downloader_root"] = settings["downloader_root"]
    result["models"]["base_url"] = result["models"].get("base_url") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    result["_api_key"] = api_key(course, settings)
    options_path = course / "course-options.json"
    options = course_options_record(course)
    result["_settings_error"] = settings.get(ERROR) or options.get(ERROR, "")
    if result["_settings_error"]:
        result["_api_key"] = ""
    result["course"] = {**config.get("course", {}), **{k: options[k] for k in ("profile", "terms") if k in options}}
    return result


def save_course_options(course: Path, values: dict) -> None:
    profile = str(values.get("profile", "general"))
    if profile not in {"general", "stem", "economics", "humanities", "law", "language"}:
        raise SettingsError("请选择有效课程模板。")
    terms = values.get("terms", [])
    if not isinstance(terms, list) or len(terms) > 500 or any(not isinstance(v, str) or len(v) > 200 for v in terms):
        raise SettingsError("术语应为字符串列表，最多500项，每项不超过200字。")
    from .core import atomic_write, write_json
    if course_options_record(course).get(ERROR):
        backup_record(course / "course-options.json")
    write_json(course / "course-options.json", {"profile": profile, "terms": list(dict.fromkeys(v.strip() for v in terms if v.strip()))})
    if "prompt" in values:
        atomic_write(course / "prompt.md", str(values["prompt"]))


def remember_course(course: Path) -> None:
    settings = load_settings(course)
    if settings.get(ERROR):
        return
    settings["current_course"] = course.name
    from .core import write_json
    write_json(settings_path(course), settings)


def save_settings(course: Path, values: dict) -> None:
    settings = load_settings(course)
    was_invalid = bool(settings.get(ERROR))
    if was_invalid:
        settings = {}
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
    if was_invalid:
        backup_record(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

"""Read damaged UI records without destroying originals or enabling AI silently."""
import json
import shutil
import uuid

ERROR = "_record_error"


def read_record(path):
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("not an object")
        return value
    except (OSError, ValueError, UnicodeError):
        return {ERROR: f"{path.name} 无法读取或格式损坏，原文件未改动。请重新配置或恢复备份。"}


def backup_record(path):
    if path.is_file():
        backup = path.with_name(path.name + ".corrupt-" + uuid.uuid4().hex[:12] + ".bak")
        shutil.copy2(path, backup)
        return backup

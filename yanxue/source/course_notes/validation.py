"""Local structural checks. Passing these is not a semantic correctness guarantee."""
import json
import math

from .core import PipelineError


def validate_schema(value, schema, location="返回内容"):
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str}
    if kind in types and not isinstance(value, types[kind]):
        raise PipelineError(f"{location}格式错误：应为 {kind}，请检查模型是否支持结构化输出后重试。")
    if kind == "object":
        for key in schema.get("required", []):
            if key not in value:
                raise PipelineError(f"{location}缺少字段 {key}，请检查模型兼容性后重试。")
        for key, item in value.items():
            if key in schema.get("properties", {}):
                validate_schema(item, schema["properties"][key], location + "." + key)
    elif kind == "array":
        for i, item in enumerate(value):
            validate_schema(item, schema["items"], f"{location}[{i}]")
    elif kind == "string" and not value.strip():
        raise PipelineError(f"{location}为空，未通过结果检验，请重试。")


def validate_transcription(value):
    if not isinstance(value, dict) or not isinstance(value.get("text"), str) or not value["text"].strip():
        raise PipelineError("转录结果为空或格式错误（也可能是静音片段），请试听对应音频并检查转录模型后重试。")


def validate_transcript(value, duration):
    if not isinstance(value, dict) or not isinstance(value.get("chunks"), list) or not value["chunks"]:
        raise PipelineError("转录分段索引为空或损坏。")
    end = 0.0
    for part in value["chunks"]:
        validate_transcription(part)
        start, stop = part.get("start"), part.get("end")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in [start, stop]):
            raise PipelineError("转录时间戳格式错误。")
        if abs(start - end) > 0.2 or stop <= start:
            raise PipelineError("转录时间段有缺口、重叠或倒序。")
        end = stop
    if duration <= 0 or abs(end - duration) > max(3, duration * .02):
        raise PipelineError("转录分段总时长与原音视频不符，请检查文件完整性后重试。")


def cached_result(path, validate):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        validate(value)
        return value
    except (PipelineError, ValueError, TypeError, OSError):
        return None


def validate_review(value, schema):
    validate_schema(value, schema)
    if not value["quiz"] and not value["cards"]:
        raise PipelineError("复习包没有任何自测题或卡片，未通过结果检验。")

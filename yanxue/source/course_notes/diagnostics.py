"""Allowlisted diagnostic metadata: no paths, raw logs, notes, keys or account IDs."""
import shutil
import time
from collections import Counter

from .desktop import diagnostics
from .records import read_record


def failure_info(message):
    value = str(message).lower()
    rules = [
        (("401", "密钥", "api key"), "AI_AUTH", "在连接设置更新密钥，并保存测试；之后从断点重试。"),
        (("403", "没有权限"), "AI_PERMISSION", "检查所用服务、模型和账号权限。"),
        (("429", "额度", "次数上限"), "AI_LIMIT", "检查请求额度与供应商余额，确认后再手动恢复。"),
        (("网络", "连接中断", "network", "timeout", "超时"), "NETWORK", "检查网络与服务状态，恢复后从断点重试。"),
        (("静音", "转录结果为空"), "EMPTY_TRANSCRIPT", "试听对应音频，确认有讲授内容；检查转录模型与接口。"),
        (("时间戳", "缺口", "总时长", "音频切片"), "MEDIA_COVERAGE", "检查原录播能否完整播放、FFmpeg 是否可用。"),
        (("材料", "文件为空", "找不到", "丢失", "ocr"), "INPUT", "恢复原材料位置；扫描件先补充 OCR 文本，空文件重新下载。"),
        (("字段", "格式", "json", "结构化", "响应", "复习包没有"), "OUTPUT_FORMAT", "检查模型与接口兼容性；重试会只重新请求无效分段。"),
        (("磁盘", "space", "permission", "拒绝访问"), "LOCAL_STORAGE", "检查可用磁盘空间、文件权限和安全软件拦截。"),
    ]
    for needles, code, suggestion in rules:
        if any(needle in value for needle in needles):
            return {"code":code, "suggestion":suggestion}
    return {"code":"UNKNOWN", "suggestion":"查看失败阶段与本机运行记录，保留原材料和已完成分段后再重试。"}


def diagnostic_report(app):
    runtime = diagnostics()
    usage = read_record(app.workspace_root.parent / "request-usage.json")
    checks = Counter()
    for item in app.list_lectures():
        state = item.get("validation", {}).get("status", "unchecked")
        checks[state if state in {"passed", "warning", "failed", "running"} else "unchecked"] += 1
    safe_usage = {key: usage[key] for key in ["used", "limit"] if isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool)}
    return {"format_version":1, "generated_at":time.time(), "runtime":runtime,
        "disk_free_mb":shutil.disk_usage(app.course_dir).free // (1024 * 1024),
        "lecture_validation_counts":dict(checks), "request_usage":safe_usage,
        "last_failure":failure_info(app.runner.error) if app.runner.error else None,
        "privacy":"不含密钥、账号、课程名称、文件路径、笔记、音频、原始错误文本或日志；未自动上传。"}

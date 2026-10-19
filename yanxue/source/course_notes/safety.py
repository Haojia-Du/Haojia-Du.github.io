"""Per-task network request limits; estimates are not provider bills."""
from contextlib import contextmanager
from contextvars import ContextVar

from .core import PipelineError, write_json

_guard = ContextVar("yanxue_request_guard", default=None)


def before_request():
    guard = _guard.get()
    if guard is None:
        return
    if guard["used"] >= guard["limit"]:
        raise PipelineError("已达到本次任务 AI 请求次数上限，任务已停止。已完成内容保留；确认后可重试并重新分配额度。")
    guard["used"] += 1
    write_json(guard["path"], {k: v for k, v in guard.items() if k != "path"})


@contextmanager
def request_limit(path, limit):
    guard = {"path": path, "limit": limit, "used": 0}
    write_json(path, {"limit": limit, "used": 0})
    token = _guard.set(guard)
    try:
        yield
    finally:
        _guard.reset(token)


def sample_lesson(app, source, sample_id):
    """Only local media is clipped; original material is never modified."""
    from pathlib import Path
    from .core import MEDIA_EXTENSIONS, CoursePipeline, import_lecture, run_checked
    source = Path(source).resolve()
    if not source.is_file() or source.suffix.lower() not in MEDIA_EXTENSIONS:
        raise PipelineError("请选择已下载的本地音视频文件。")
    if not app.config.get("_api_key"):
        raise PipelineError("请先在连接设置中填写并测试 AI Key。")
    folder = app.course_dir / "samples" / sample_id
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / "前3分钟.mp3"
    if not clip.exists():
        temporary = folder / "partial.mp3"
        run_checked(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
                     "-t", "180", "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(temporary)])
        temporary.replace(clip)
    lecture = import_lecture(app.course_dir, "试跑-" + folder.name, [clip], link=True)
    CoursePipeline(app.course_dir).build(lecture, offline=False)
    return "前3分钟试跑完成，请在笔记书架核对转录、笔记和质检报告；原文件未改动。"

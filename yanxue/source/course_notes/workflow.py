"""Persistent recording downloads, resumable without downloading successful jobs again."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode, quote

from .core import CoursePipeline, PipelineError, downloader_output_root, import_lecture, write_json


def recording_identity(course_id: str, rec: dict, include_files: bool) -> str:
    return hashlib.sha256(json.dumps([course_id, rec.get("name"), rec.get("time"), include_files], ensure_ascii=False).encode()).hexdigest()[:20]


def run_recording(app, course_id: str, recording_index: int, include_files: bool, profile: str, snapshot: dict | None = None, additional_sources: list[Path] | None = None) -> str:
    course = snapshot or next((c for c in app.recorder_courses if str(c.get("course_id")) == str(course_id)), None)
    if course is None or recording_index < 0 or recording_index >= len(course.get("recordings", [])):
        raise PipelineError("课程列表已过期，请重新读取课程并选择录播。")
    rec = course["recordings"][recording_index]
    title = course.get("name", "未命名课程")
    app._activate_course(title, profile)
    identity = recording_identity(course_id, rec, include_files)
    checkpoint_path = app.course_dir / (".recording-" + identity + ".json")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.exists() else {}
    if checkpoint.get("lecture"):
        lecture = app._lecture_path(checkpoint["lecture"])
    else:
        app.one_click_stage = "collecting"
        if not app.recorder_status(force=True).get("running"):
            app.start_recorder()
        status = app._recorder_api("/api/bridge/status", timeout=10)
        tracked_ids = set(checkpoint.get("job_ids", []))
        if not tracked_ids:
            items = [{"type": "video", "course_id": course_id, "course_name": title,
                      "name": rec.get("name", "课堂实录"), "time": rec.get("time", ""), "teacher": rec.get("teacher", "")}]
            if include_files:
                files = app._recorder_api("/api/course_files?" + urlencode({"course_id": course_id, "course_name": title}), timeout=360).get("files", [])
                items += [{"type": "file", "course_id": course_id, "course_name": title,
                           "name": f.get("name", "课程文件"), "url": f.get("url", ""), "source": f.get("source", "")} for f in files]
            before_ids = {str(j.get("id")) for j in status.get("jobs", [])}
            app._recorder_api("/api/download", method="POST", payload={"items": items})
            status = app._recorder_api("/api/bridge/status", timeout=10)
            tracked_ids = {str(j.get("id")) for j in status.get("jobs", []) if str(j.get("id")) not in before_ids and str(j.get("course_id")) == str(course_id)}
            if len(tracked_ids) != len(items):
                raise PipelineError("下载任务返回数量不一致，请检查下载器任务列表后重试。")
            checkpoint["job_ids"] = sorted(tracked_ids)
            write_json(checkpoint_path, checkpoint)
        else:
            tracked = [j for j in status.get("jobs", []) if str(j.get("id")) in tracked_ids]
            if len(tracked) != len(tracked_ids):
                raise PipelineError("原下载任务已被清除，请在下载器恢复任务或重新选择录播。")
            for job in tracked:
                if job.get("status") in {"failed", "canceled"}:
                    app._recorder_api("/api/retry/" + quote(str(job["id"]), safe=""), method="POST", payload={})
        app.one_click_stage = "downloading"
        last = ""
        last_progress = time.monotonic()
        for _ in range(2700):
            status = app._recorder_api("/api/bridge/status", timeout=10)
            tracked = [j for j in status.get("jobs", []) if str(j.get("id")) in tracked_ids]
            if len(tracked) != len(tracked_ids):
                raise PipelineError("下载任务丢失，请检查教学网后台。")
            summary = "；".join(f"{j.get('rec_name', '任务')} {j.get('progress', 0)}%" for j in tracked)
            if summary != last:
                app.events.add("下载进度：" + summary)
                last = summary
                last_progress = time.monotonic()
            if all(j.get("status") == "done" for j in tracked):
                break
            if any(j.get("status") in {"failed", "canceled"} for j in tracked):
                raise PipelineError("部分下载未完成，点击重试会仅重启失败任务。")
            if time.monotonic() - last_progress > 900:
                raise PipelineError("下载进度已15分钟未变化，可能卡住；请检查网络和下载器后重试，原任务会保留，不重新创建下载。")
            time.sleep(4)
        else:
            raise PipelineError("下载等待超时，任务已保留，可稍后重试。")
        output = Path(status.get("settings", {}).get("download_dir") or downloader_output_root(app.downloader_root))
        paths = [output / str(job.get("file", "")) for job in tracked]
        if not all(path.is_file() for path in paths):
            raise PipelineError("下载器显示完成，但部分文件无法读取，请检查下载目录。")
        lecture_title = " ".join(filter(None, [rec.get("time", ""), rec.get("name", "课堂实录")]))
        lecture = import_lecture(app.course_dir, lecture_title + "_" + identity[:6], paths, link=True)
        checkpoint["lecture"] = lecture.name
        write_json(checkpoint_path, checkpoint)
    if additional_sources:
        import_lecture(app.course_dir, lecture.name, additional_sources, link=True)
    app.one_click_stage = "generating"
    app.events.add("开始转录与笔记生成；已完成的音频段和笔记段会自动复用。")
    CoursePipeline(app.course_dir).build(lecture, offline=False)
    app.one_click_stage = "done"
    return title + "｜" + lecture.name + " 已生成"

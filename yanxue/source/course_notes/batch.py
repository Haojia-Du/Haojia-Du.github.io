"""Opt-in course queues. Pause only between lessons; keep successful work on resume."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .core import PipelineError, write_json
from .materials import lecture_files
from .study import generate_review
from .workflow import recording_identity


def batch_path(app) -> Path:
    return app.course_dir / ".batch-progress.json"


def batch_status(app) -> dict:
    path = batch_path(app)
    if not path.exists():
        return {"available": False}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("state") == "running" and not app.runner.running:
        data["state"] = "paused"
    return {"available": True, **data}


def prepare_batch(app, course: dict, indices: list[int], profile: str, include_files: bool, review: bool) -> dict:
    if not indices or len(indices) != len(set(indices)):
        raise PipelineError("请至少选择一节录播，且不要重复选择。")
    recordings = course.get("recordings", [])
    if any(i < 0 or i >= len(recordings) for i in indices):
        raise PipelineError("课程录播列表发生变化，请刷新后选择。")
    app._activate_course(course["name"], profile)
    existing = batch_status(app)
    if existing.get("available") and existing.get("state") not in {"done"}:
        raise PipelineError("这门课已有未完成批次，请先继续该批次。")
    snapshot = {"course_id": str(course["course_id"]), "name": course["name"], "recordings": [
        {key: recordings[i].get(key, "") for key in ("name", "time", "teacher")} for i in indices]}
    data = {"id": uuid.uuid4().hex[:12], "course": snapshot, "profile": profile, "include_files": include_files,
            "generate_review": review, "state": "queued", "created_at": time.time(), "error": "", "shared_files": [],
            "items": [{"index": i, "name": r["name"], "time": r["time"], "state": "pending", "lecture": "", "error": ""}
                      for i, r in enumerate(snapshot["recordings"])]}
    write_json(batch_path(app), data)
    return {"batch_id": data["id"]}


def run_batch(app, identifier: str) -> str:
    path = batch_path(app)
    if not path.exists():
        raise PipelineError("找不到该批次，请重新选择课程。")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["id"] != identifier:
        raise PipelineError("批次已发生变化，请刷新。")
    data.update(state="running", error="")
    write_json(path, data)
    for item in data["items"]:
        if item["state"] == "done":
            continue
        if app.batch_pause.is_set():
            data["state"] = "paused"
            write_json(path, data)
            return "批量任务已暂停，当前已完成的课程单元会保留"
        item.update(state="running", error="")
        write_json(path, data)
        include = bool(data["include_files"] and item["index"] == 0)
        try:
            app.events.add(f"批量进度 {item['index'] + 1}/{len(data['items'])}：{item['name']}")
            app.one_click_process(str(data["course"]["course_id"]), item["index"], include, data["profile"], data["course"],
                                  [Path(p) for p in data.get("shared_files", [])])
            rec = data["course"]["recordings"][item["index"]]
            identity = recording_identity(data["course"]["course_id"], rec, include)
            checkpoint = json.loads((app.course_dir / (".recording-" + identity + ".json")).read_text(encoding="utf-8"))
            item["lecture"] = checkpoint["lecture"]
            lecture = app._lecture_path(item["lecture"])
            if item["index"] == 0 and data["include_files"]:
                data["shared_files"] = [str(p) for p in lecture_files(lecture) if p.suffix.lower() in {".pdf", ".pptx", ".docx", ".txt", ".md"}]
            item["state"] = "notes_done"
            write_json(path, data)
            if data["generate_review"]:
                app.one_click_stage = "reviewing"
                generate_review(app.course_dir, lecture)
            item.update(state="done", completed_at=time.time())
            write_json(path, data)
        except Exception as exc:
            item.update(state="error", error=str(exc))
            data.update(state="error", error=str(exc))
            write_json(path, data)
            raise
    data.update(state="done", completed_at=time.time())
    write_json(path, data)
    app.one_click_stage = "done"
    return f"整门课批次已完成：{len(data['items'])} 节"

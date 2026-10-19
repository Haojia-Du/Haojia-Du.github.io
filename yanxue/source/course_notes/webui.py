from __future__ import annotations

import json
import hashlib
import mimetypes
import re
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

from .core import (
    CoursePipeline,
    OpenAIProvider,
    PipelineError,
    atomic_write,
    discover_downloader_materials,
    import_lecture,
    init_course,
    read_toml,
    slugify,
    write_json,
)
from .cli import sync_once
from .settings import effective_config, load_settings, save_settings, settings_path, save_course_options, remember_course
from .materials import material_data, file_at
from .study import generate_review
from .workflow import run_recording
from .batch import batch_status, prepare_batch, run_batch, edit_batch
from .safety import request_limit, sample_lesson
from .desktop import diagnostics, install_browser, install_ffmpeg, check_update
from .version import VERSION


WEB_ROOT = Path(__file__).with_name("web")


class EventLog:
    def __init__(self, limit: int = 400):
        self._items: deque[dict[str, Any]] = deque(maxlen=limit)
        self._lock = threading.Lock()
        self._counter = 0

    def add(self, message: str, level: str = "info") -> None:
        with self._lock:
            self._counter += 1
            self._items.append({
                "id": self._counter,
                "time": time.strftime("%H:%M:%S"),
                "level": level,
                "message": message,
            })

    def snapshot(self, after: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [item.copy() for item in self._items if item["id"] > after]


class BackgroundRunner:
    def __init__(self, events: EventLog, task_path: Path | None = None):
        self.events = events
        self._lock = threading.Lock()
        self.running = False
        self.action = ""
        self.error = ""
        self.finished_at: float | None = None
        self.task_path = task_path
        self.task: dict[str, Any] = {}
        if task_path and task_path.exists():
            self.task = json.loads(task_path.read_text(encoding="utf-8"))
            self.action = self.task.get("action", "")
            self.error = self.task.get("error", "")
            if self.task.get("state") == "running":
                self.error = "上次运行被中断，点击重试可继续处理。"

    def _persist(self, state: str) -> None:
        self.task.update(state=state, action=self.action, error=self.error)
        if self.task_path:
            write_json(self.task_path, self.task)

    def start(self, action: str, function: Callable[[], Any], task: dict | None = None) -> None:
        with self._lock:
            if self.running:
                raise PipelineError(f"已有任务正在执行：{self.action}")
            self.running = True
            self.action = action
            self.error = ""
            self.finished_at = None
            self.task = task or {}
            self._persist("running")

        def target() -> None:
            self.events.add(f"开始：{action}")
            try:
                result = function()
                if result is not None:
                    self.events.add(f"完成：{action}｜{result}", "success")
                else:
                    self.events.add(f"完成：{action}", "success")
            except Exception as exc:  # UI must surface failures instead of dying.
                self.error = str(exc)
                self.events.add(f"失败：{action}｜{exc}", "error")
                self.events.add(traceback.format_exc(limit=4), "debug")
            finally:
                with self._lock:
                    self.running = False
                    self.finished_at = time.time()
                    self._persist("failed" if self.error else "done")

        threading.Thread(target=target, name=f"course-notes-{action}", daemon=True).start()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self.running,
                "action": self.action,
                "error": self.error,
                "finished_at": self.finished_at,
                "can_retry": bool(self.error and self.task.get("kind")),
            }


class WatchController:
    def __init__(self, app: "WebApp"):
        self.app = app
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.offline = False
        self.error = ""

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self, offline: bool = False, limit: int = 100) -> None:
        if self.running:
            return
        if self.app.runner.running:
            raise PipelineError("请等待当前任务完成后再开启自动监听。")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise PipelineError("AI 请求次数上限应为1～10000的整数。")
        self.offline = offline
        self.error = ""
        self._stop.clear()

        def loop() -> None:
            mode = "离线" if offline else "正式"
            self.app.events.add(f"自动监听已开启（{mode}模式）。", "success")
            with request_limit(self.app.workspace_root.parent / "request-usage.json", limit, self._stop):
                while not self._stop.is_set():
                    try:
                        imported = sync_once(
                            self.app.course_dir, self.app.course_title, build=True, offline=offline,
                        )
                        if imported:
                            self.app.events.add(f"自动处理了 {len(imported)} 个新增材料组。", "success")
                    except Exception as exc:
                        self.error = str(exc)
                        self.app.events.add(f"自动监听已停止：{exc}。修复后请手动重新开启。", "error")
                        self._stop.set()
                    self._stop.wait(self.app.watch_interval)
            self.app.events.add("自动监听已停止。")

        self._thread = threading.Thread(target=loop, name="course-notes-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class WebApp:
    def __init__(self, course_dir: Path):
        self.course_dir = course_dir.resolve()
        self.config_path = self.course_dir / "course.toml"
        if not self.config_path.exists():
            raise PipelineError(f"找不到课程配置：{self.config_path}")
        self.events = EventLog()
        self.runner = BackgroundRunner(self.events, settings_path(self.course_dir).with_name("local-task.json"))
        self.watcher = WatchController(self)
        self.api_validation = "unknown"
        self.recorder_courses: list[dict[str, Any]] = []
        self.recorder_process: subprocess.Popen[Any] | None = None
        self.one_click_stage = "idle"
        self.workspace_root = self.course_dir.parent
        self.batch_pause = threading.Event()
        self.batch_cancel = threading.Event()
        self._recorder_cache: tuple[float, dict[str, Any]] = (0.0, {"running": False})
        self.events.add("可视化工作台已启动。", "success")

    @property
    def config(self) -> dict[str, Any]:
        return effective_config(self.course_dir, read_toml(self.config_path))

    @property
    def course_title(self) -> str:
        return self.config.get("course", {}).get("title", self.course_dir.name)

    @property
    def watch_interval(self) -> int:
        return max(10, int(self.config.get("automation", {}).get("settle_seconds", 30)))

    @property
    def downloader_root(self) -> Path | None:
        value = self.config.get("automation", {}).get("downloader_root", "")
        return Path(value) if value else None

    def _lecture_path(self, name: str) -> Path:
        root = (self.course_dir / "lectures").resolve()
        candidate = (root / name).resolve()
        if candidate.parent != root or not candidate.is_dir():
            raise PipelineError("课程目录不存在或路径非法。")
        return candidate

    def list_lectures(self) -> list[dict[str, Any]]:
        root = self.course_dir / "lectures"
        result: list[dict[str, Any]] = []
        if not root.exists():
            return result
        for lecture in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda p: p.name):
            source = lecture / "source"
            local_files = [path for path in source.iterdir() if path.is_file() and not path.name.startswith(".")] if source.exists() else []
            refs = []
            ref_path = source / ".external-sources.json"
            if ref_path.exists():
                try:
                    refs = json.loads(ref_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    refs = []
            draft = lecture / "generated" / "draft.md"
            qa = lecture / "generated" / "qa_report.md"
            final = lecture / "final" / "notes.md"
            state = lecture / ".course-notes-state.json"
            state_data: dict[str, Any] = {}
            validation = {}
            validation_path = lecture / "generated" / "validation.json"
            if validation_path.exists():
                try:
                    validation = json.loads(validation_path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    validation = {"status": "failed", "stage": "结果检验", "error": "检验报告损坏，请重新生成。"}
            if state.exists():
                try:
                    state_data = json.loads(state.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            if final.exists():
                status = "final"
            elif draft.exists():
                status = "offline" if state_data.get("offline") else "draft"
            else:
                status = "pending"
            result.append({
                "name": lecture.name,
                "validation": validation,
                "sources": len(local_files) + len(refs),
                "status": status,
                "has_draft": draft.exists(),
                "has_qa": qa.exists(),
                "has_final": final.exists(),
                "updated_at": max((path.stat().st_mtime for path in [draft, final, state] if path.exists()), default=lecture.stat().st_mtime),
            })
        return result

    def status(self) -> dict[str, Any]:
        downloader = self.downloader_root
        materials = []
        if downloader and downloader.exists():
            try:
                materials = discover_downloader_materials(downloader, self.course_title)
            except OSError:
                pass
        recorder = self.recorder_status()
        return {
            "version": VERSION,
            "request_usage": json.loads((self.workspace_root.parent / "request-usage.json").read_text(encoding="utf-8")) if (self.workspace_root.parent / "request-usage.json").exists() else {},
            "batch": batch_status(self),
            "course": {
                "title": self.course_title,
                "profile": self.config.get("course", {}).get("profile", "general"),
                "path": str(self.course_dir),
            },
            "environment": {
                "api_key_present": bool(self.config.get("_api_key")),
                "api_key_status": self.api_validation,
                "ffmpeg": bool(shutil.which("ffmpeg")),
                "pandoc": bool(shutil.which("pandoc")),
                "downloader_configured": bool(downloader),
                "downloader_exists": bool(downloader and downloader.exists()),
                "detected_materials": len(materials),
            },
            "lectures": self.list_lectures(),
            "job": self.runner.status(),
            "watcher": {"running": self.watcher.running, "offline": self.watcher.offline, "error": self.watcher.error},
            "recorder": {
                "running": recorder.get("running", False),
                "logged_in": recorder.get("logged_in", False),
                "username": recorder.get("username", ""),
                "semester": recorder.get("semester", ""),
                "jobs": recorder.get("stats", {}),
                "courses": self.recorder_courses,
                "one_click_stage": self.one_click_stage,
            },
        }

    def sync(self, offline: bool) -> str:
        imported = sync_once(self.course_dir, self.course_title, build=True, offline=offline)
        return f"导入并处理 {len(imported)} 个材料组"

    def build(self, lecture: str, offline: bool, force: bool) -> str:
        output = CoursePipeline(self.course_dir).build(self._lecture_path(lecture), offline=offline, force=force)
        return str(output)

    def finalize(self, lecture: str) -> str:
        path = self._lecture_path(lecture)
        source = path / "generated" / "draft.md"
        if not source.exists():
            raise PipelineError("该课程尚无初稿。")
        if not source.read_text(encoding="utf-8").strip():
            raise PipelineError("初稿为空，不能定稿，请重新生成。")
        report = path / "generated" / "validation.json"
        if report.exists() and json.loads(report.read_text(encoding="utf-8")).get("status") in {"failed", "running"}:
            raise PipelineError("本次处理尚未通过检验，请先重新检验并续跑，或在学习台人工核对编辑后保存定稿。")
        target = path / "final" / "notes.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return str(target)

    def import_files(self, title: str, paths: list[str]) -> str:
        if not title.strip():
            raise PipelineError("请填写课程小节名称。")
        lecture = import_lecture(self.course_dir, title.strip(), [Path(value) for value in paths], link=True)
        return str(lecture)

    def check_key(self) -> str:
        try:
            OpenAIProvider(api_key=self.config.get("_api_key"), base_url=self.config.get("models", {}).get("base_url")).validate_key()
            self.api_validation = "valid"
            return "API Key 可用"
        except Exception:
            self.api_validation = "invalid"
            raise

    def schedule(self, kind: str, args: dict[str, Any], retry: bool = False) -> None:
        if self.runner.running:
            raise PipelineError("已有任务正在执行，请等待完成。")
        if self.watcher.running:
            raise PipelineError("请先停止自动监听，再执行手动任务。")
        requested_limit = int(args.get("request_limit", 100))
        if not 1 <= requested_limit <= 10000:
            raise PipelineError("AI 请求次数上限应在 1～10000 之间。")
        if retry:
            task = self.runner.task
            kind, args = task.get("kind", ""), task.get("args", {})
            course = Path(task.get("course", str(self.course_dir))).resolve()
            if course.parent != self.workspace_root:
                raise PipelineError("重试任务的课程目录无效。")
            self.course_dir, self.config_path = course, course / "course.toml"
        if kind == "batch":
            if not retry and "batch_id" not in args:
                selected = next((c for c in self.recorder_courses if str(c.get("course_id")) == str(args.get("course_id"))), None)
                if selected is None:
                    raise PipelineError("请先读取教学网课程。")
                args = prepare_batch(self, selected, [int(i) for i in args.get("indices", [])], str(args.get("profile", "general")),
                                     bool(args.get("include_files", True)), bool(args.get("generate_review", False)))
                args["request_limit"] = requested_limit
            self.batch_pause.clear()
            self.batch_cancel.clear()
            function = lambda: run_batch(self, str(args["batch_id"]))
        elif kind == "one-click":
            if not retry:
                selected = next((c for c in self.recorder_courses if str(c.get("course_id")) == str(args.get("course_id"))), None)
                if selected is None:
                    raise PipelineError("请先读取课程。")
                index = int(args.get("recording_index", -1))
                if index < 0 or index >= len(selected.get("recordings", [])):
                    raise PipelineError("请选择有效录播。")
                rec = selected["recordings"][index]
                args = {**args, "snapshot": {"course_id": selected["course_id"], "name": selected["name"],
                        "recordings": [{k: rec.get(k, "") for k in ("name", "time", "teacher")} ]}, "recording_index": 0}
            function = lambda: self.one_click_process(str(args["course_id"]), int(args["recording_index"]), bool(args.get("include_files", True)), str(args.get("profile", "general")), args["snapshot"])
        elif kind == "build":
            function = lambda: self.build(str(args["lecture"]), bool(args.get("offline")), bool(args.get("force")))
        elif kind == "sync":
            function = lambda: self.sync(bool(args.get("offline")))
        elif kind == "review":
            function = lambda: (generate_review(self.course_dir, self._lecture_path(str(args["lecture"]))), "复习包已生成")[1]
        elif kind == "sample":
            import uuid
            args.setdefault("sample_id", uuid.uuid4().hex[:12])
            function = lambda: sample_lesson(self, str(args.get("source", "")), args["sample_id"])
        elif kind == "browser-install":
            function = install_browser
        elif kind == "ffmpeg-install":
            function = install_ffmpeg
        else:
            raise PipelineError("没有可重试任务，请从课程单元重新启动。")
        labels = {"one-click": "一键完成录播学习资料", "build": "生成课程笔记", "sync": "同步并整理材料", "review": "生成复习包", "batch": "批量整理整门课", "browser-install": "安装浏览器组件", "ffmpeg-install": "安装 FFmpeg 组件"}
        labels["sample"] = "前3分钟小样本试跑"
        limit = int(args.get("request_limit", 100))
        if not 1 <= limit <= 10000:
            raise PipelineError("AI 请求次数上限应在 1～10000 之间。")
        args["request_limit"] = limit
        task_function = function
        def guarded():
            with request_limit(self.workspace_root.parent / "request-usage.json", limit):
                return task_function()
        self.runner.start(labels[kind], guarded, {"kind": kind, "args": args, "course": str(self.course_dir)})

    def settings_data(self) -> dict[str, Any]:
        config = self.config
        saved = load_settings(self.course_dir)
        return {"downloader_root": str(self.downloader_root or ""), "models": {**config.get("models", {}), "base_url": config.get("models", {}).get("base_url") or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")},
                "has_key": bool(config.get("_api_key")), "remembered": bool(saved.get("encrypted_api_key")),
                "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe"))}

    def _recorder_api(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        timeout: int = 30,
        require_ok: bool = True,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = urllib.request.Request(
            "http://127.0.0.1:5000" + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PipelineError("PKU-Recorder 后台尚未启动或无法连接。") from exc
        if require_ok and not data.get("ok", False):
            raise PipelineError(data.get("error") or "PKU-Recorder 请求失败。")
        return data

    def recorder_status(self, force: bool = False) -> dict[str, Any]:
        cached_at, cached = self._recorder_cache
        if not force and time.time() - cached_at < 1.2:
            return cached
        try:
            status = self._recorder_api("/api/bridge/status", timeout=1, require_ok=False)
            status["running"] = True
        except PipelineError:
            status = {"running": False, "logged_in": False, "stats": {}}
        self._recorder_cache = (time.time(), status)
        return status

    def start_recorder(self) -> str:
        if self.recorder_status(force=True).get("running"):
            return "教学网后台已经运行"
        root = self.downloader_root
        if root is None:
            raise PipelineError("尚未配置 PKU-Recorder 路径。")
        app_path = root / "pku_recorder_app" / "app.py"
        if not app_path.exists():
            raise PipelineError(f"找不到 PKU-Recorder 后台：{app_path}")
        environment = os.environ.copy()
        environment["PKU_QT_MODE"] = "1"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        command = [sys.executable, "--recorder-bridge", str(app_path)] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).with_name("recorder_bridge.py")), str(app_path)]
        self.recorder_process = subprocess.Popen(
            command,
            cwd=app_path.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        for _ in range(120):
            time.sleep(0.5)
            if self.recorder_status(force=True).get("running"):
                return "教学网后台已启动"
            if self.recorder_process.poll() is not None:
                break
        raise PipelineError("PKU-Recorder 后台未能在 60 秒内启动。")

    def recorder_login(self, username: str, password: str, semester: str) -> str:
        if not username or not password:
            raise PipelineError("请输入学号和 IAAA 密码。")
        if not self.recorder_status(force=True).get("running"):
            self.start_recorder()
        self._recorder_api(
            "/api/login",
            method="POST",
            payload={"username": username, "password": password, "semester": semester},
            timeout=180,
        )
        self._recorder_cache = (0.0, {})
        return "教学网登录成功"

    def recorder_login_and_load(self, username: str, password: str, semester: str) -> str:
        self.recorder_login(username, password, semester)
        return self.load_recorder_courses(semester)

    def load_recorder_courses(self, semester: str = "") -> str:
        if not self.recorder_status(force=True).get("logged_in"):
            raise PipelineError("请先在工作台内登录教学网。")
        query = "?" + urlencode({"semester": semester}) if semester else ""
        data = self._recorder_api("/api/courses" + query, timeout=360)
        self.recorder_courses = data.get("courses", [])
        count = sum(len(course.get("recordings", [])) for course in self.recorder_courses)
        return f"读取到 {len(self.recorder_courses)} 门课程、{count} 条录播"

    def _activate_course(self, title: str, profile: str) -> Path:
        if profile not in {"general", "economics", "stem", "humanities", "law", "language"}:
            raise PipelineError("请选择有效课程模板。")
        target = self.workspace_root / slugify(title)
        if not (target / "course.toml").exists():
            root = str(self.downloader_root) if self.downloader_root else ""
            init_course(target, title, profile, root)
        self.course_dir = target.resolve()
        self.config_path = self.course_dir / "course.toml"
        save_course_options(self.course_dir, {"profile": profile, "terms": self.config.get("course", {}).get("terms", [])})
        remember_course(self.course_dir)
        return self.course_dir

    def one_click_process(self, course_id: str, recording_index: int, include_files: bool, profile: str, snapshot: dict | None = None, additional_sources: list[Path] | None = None) -> str:
        try:
            return run_recording(self, course_id, recording_index, include_files, profile, snapshot, additional_sources)
        except Exception:
            self.one_click_stage = "error"
            raise

    def read_note(self, lecture: str, kind: str) -> dict[str, Any]:
        base = self._lecture_path(lecture)
        choices = {
            "draft": base / "generated" / "draft.md",
            "qa": base / "generated" / "qa_report.md",
            "final": base / "final" / "notes.md",
            "review": base / "generated" / "review.md",
        }
        path = choices.get(kind)
        if path is None or not path.exists():
            raise PipelineError("目标内容尚不存在。")
        return {"lecture": lecture, "kind": kind, "content": path.read_text(encoding="utf-8"), "path": str(path)}

    def save_note(self, lecture: str, content: str, finalize: bool) -> str:
        base = self._lecture_path(lecture)
        if finalize and not content.strip():
            raise PipelineError("空白笔记不能保存为定稿，请先补全内容。")
        if finalize:
            path = base / "final" / "notes.md"
        else:
            path = base / "generated" / "draft.md"
        atomic_write(path, content)
        return str(path)

    def local_courses(self) -> list[dict]:
        result = []
        for path in sorted(self.workspace_root.iterdir()):
            if path.is_dir() and (path / "course.toml").is_file():
                config = effective_config(path, read_toml(path / "course.toml"))
                result.append({"name": path.name, "title": config.get("course", {}).get("title", path.name), "active": path == self.course_dir})
        return result

    def select_course(self, name: str) -> None:
        if self.runner.running or self.watcher.running:
            raise PipelineError("请先等待当前任务完成或暂停，再切换课程。")
        target = (self.workspace_root / name).resolve()
        if target.parent != self.workspace_root or not (target / "course.toml").is_file():
            raise PipelineError("课程工作区不存在。")
        self.course_dir, self.config_path = target, target / "course.toml"
        remember_course(self.course_dir)

    def course_options(self, title: str = "") -> dict:
        target = self.workspace_root / slugify(title) if title else self.course_dir
        if not (target / "course.toml").exists():
            return {"profile": "general", "terms": [], "prompt": "", "exists": False}
        config = effective_config(target, read_toml(target / "course.toml"))
        prompt = target / "prompt.md"
        return {"profile": config["course"].get("profile", "general"), "terms": config["course"].get("terms", []),
                "prompt": prompt.read_text(encoding="utf-8") if prompt.exists() else "", "exists": True}

    def open_item(self, lecture: str, kind: str) -> str:
        if lecture:
            base = self._lecture_path(lecture)
            choices = {
                "draft": base / "generated" / "draft.md",
                "qa": base / "generated" / "qa_report.md",
                "final": base / "final" / "notes.md",
                "folder": base,
            }
            target = choices.get(kind)
        elif kind == "course":
            target = self.course_dir
        elif kind == "downloader":
            root = self.downloader_root
            candidates = [] if root is None else [
                root / "pku_recorder_app" / "dist" / "PKU-Recorder" / "PKU-Recorder.exe",
                root / "pku_recorder_app" / "qt_app.py",
            ]
            target = next((path for path in candidates if path.exists()), None)
        else:
            target = None
        if target is None or not target.exists():
            raise PipelineError("目标文件尚不存在。")
        os.startfile(str(target))  # type: ignore[attr-defined]
        return str(target)

    def pick_files(self, media_only: bool = False) -> list[str]:
        script = r'''
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Multiselect = $true
$dialog.Filter = "课程材料|*.mp4;*.m4a;*.mp3;*.wav;*.webm;*.pdf;*.pptx;*.docx;*.txt;*.md|所有文件|*.*"
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
  $dialog.FileNames | ConvertTo-Json -Compress
}
'''
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script.replace('$dialog.Multiselect = $true', '$dialog.Multiselect = $false').replace('课程材料|*.mp4;*.m4a;*.mp3;*.wav;*.webm;*.pdf;*.pptx;*.docx;*.txt;*.md', '音视频|*.mp4;*.m4a;*.mp3;*.wav;*.webm;*.mpeg;*.mpga') if media_only else script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise PipelineError(result.stderr.strip() or "文件选择器打开失败。")
        value = result.stdout.strip()
        if not value:
            return []
        parsed = json.loads(value)
        return [parsed] if isinstance(parsed, str) else parsed

    def shutdown(self) -> None:
        self.watcher.stop()
        if self.recorder_process and self.recorder_process.poll() is None:
            self.recorder_process.terminate()
            try:
                self.recorder_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.recorder_process.kill()


class Handler(SimpleHTTPRequestHandler):
    app: WebApp

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _json(self, data: Any, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1024 * 1024:
            raise PipelineError("请求体过大。")
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:
        route = urlparse(self.path)
        if route.path in {"/api/settings", "/api/materials", "/api/material", "/api/review", "/api/runtime", "/api/update", "/api/courses/local", "/api/course/options"}:
            try:
                query = parse_qs(route.query)
                if route.path == "/api/runtime":
                    self._json({"ok": True, **diagnostics()})
                    return
                if route.path == "/api/update":
                    self._json({"ok": True, **check_update()})
                    return
                if route.path == "/api/courses/local":
                    self._json({"ok": True, "courses": self.app.local_courses()})
                    return
                if route.path == "/api/course/options":
                    self._json({"ok": True, **self.app.course_options(query.get("title", [""])[0])})
                    return
                if route.path == "/api/settings":
                    self._json({"ok": True, **self.app.settings_data()})
                    return
                lecture = self.app._lecture_path(query.get("lecture", [""])[0])
                if route.path == "/api/materials":
                    self._json({"ok": True, **material_data(lecture)})
                elif route.path == "/api/material":
                    self._send_material(file_at(lecture, int(query.get("index", ["-1"])[0])))
                else:
                    review = lecture / "generated" / "review.json"
                    if not review.exists():
                        self._json({"ok": True, "available": False})
                        return
                    data = json.loads(review.read_text(encoding="utf-8"))
                    note = lecture / "final" / "notes.md"
                    if not note.exists():
                        note = lecture / "generated" / "draft.md"
                    data["stale"] = not note.exists() or hashlib.sha256(note.read_text(encoding="utf-8").encode()).hexdigest() != data.get("note_sha256")
                    progress = lecture / "review-progress.json"
                    self._json({"ok": True, "available": True, **data, "progress": json.loads(progress.read_text(encoding="utf-8")) if progress.exists() else {}})
            except (PipelineError, ValueError, OSError) as exc:
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if route.path == "/api/status":
            self._json({"ok": True, **self.app.status()})
            return
        if route.path == "/api/events":
            query = route.query.split("after=", 1)[-1] if "after=" in route.query else "0"
            try:
                after = int(query.split("&", 1)[0])
            except ValueError:
                after = 0
            self._json({"ok": True, "events": self.app.events.snapshot(after)})
            return
        if route.path == "/api/note":
            query = parse_qs(route.query)
            lecture = query.get("lecture", [""])[0]
            kind = query.get("kind", ["draft"])[0]
            try:
                self._json({"ok": True, **self.app.read_note(lecture, kind)})
            except (PipelineError, OSError) as exc:
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        super().do_GET()

    def _send_material(self, path: Path) -> None:
        size = path.stat().st_size
        start, end = 0, size - 1
        partial = self.headers.get("Range", "")
        if partial:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", partial)
            if not match or not any(match.groups()):
                self.send_error(416)
                return
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), size - 1) if right else size - 1
            else:
                start = max(0, size - int(right))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:
        try:
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                raise PipelineError("请在本机工作台页面执行此操作。")
            body = self._body()
            path = urlparse(self.path).path
            if path == "/api/sync":
                self.app.schedule("sync", {"offline": bool(body.get("offline")), "request_limit": body.get("request_limit", 100)})
            elif path == "/api/batch/start":
                self.app.schedule("batch", body)
            elif path == "/api/batch/pause":
                self.app.batch_pause.set()
            elif path == "/api/batch/cancel":
                edit_batch(self.app, "cancel")
            elif path == "/api/batch/skip":
                edit_batch(self.app, "skip")
            elif path == "/api/sample":
                if not body.get("confirmed"):
                    raise PipelineError("试跑会向配置的 AI 服务发送前3分钟音频，请先确认。")
                self.app.schedule("sample", body)
            elif path == "/api/batch/resume":
                status = batch_status(self.app)
                if not status.get("available") or status.get("state") in {"done", "canceled"}:
                    raise PipelineError("没有需要继续的批次。")
                self.app.schedule("batch", {"batch_id": status["id"], "request_limit": body.get("request_limit", 100)})
            elif path == "/api/course/select":
                self.app.select_course(str(body.get("name", "")))
            elif path == "/api/course/options":
                if self.app.runner.running or self.app.watcher.running:
                    raise PipelineError("请先结束当前任务，再修改课程模板。")
                save_course_options(self.app.course_dir, body)
            elif path == "/api/browser/install":
                self.app.schedule("browser-install", {})
            elif path == "/api/ffmpeg/install":
                self.app.schedule("ffmpeg-install", {})
            elif path == "/api/build":
                lecture = str(body.get("lecture", ""))
                offline = bool(body.get("offline"))
                force = bool(body.get("force"))
                self.app.schedule("build", {"lecture": lecture, "offline": offline, "force": force, "request_limit": body.get("request_limit", 100)})
            elif path == "/api/retry":
                self.app.schedule("", {}, retry=True)
            elif path == "/api/settings":
                if self.app.runner.running or self.app.watcher.running:
                    raise PipelineError("请等待当前处理结束，再修改连接设置。")
                save_settings(self.app.course_dir, body)
                self.app.api_validation = "unknown"
            elif path == "/api/review/generate":
                self.app.schedule("review", {"lecture": str(body.get("lecture", "")), "request_limit": body.get("request_limit", 100)})
            elif path == "/api/review/progress":
                lecture_path = self.app._lecture_path(str(body.get("lecture", "")))
                review = json.loads((lecture_path / "generated" / "review.json").read_text(encoding="utf-8"))
                index = int(body.get("index", -1))
                if index < 0 or index >= len(review.get("cards", [])) or body.get("rating") not in {"again", "known"}:
                    raise PipelineError("复习卡片记录无效。")
                progress_path = lecture_path / "review-progress.json"
                progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
                card = review["cards"][index]
                identity = hashlib.sha256(json.dumps(card, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                progress[identity] = {"rating": body["rating"], "updated_at": time.time(), "index": index}
                write_json(progress_path, progress)
            elif path == "/api/finalize":
                lecture = str(body.get("lecture", ""))
                self.app.runner.start(f"定稿 {lecture}", lambda: self.app.finalize(lecture))
            elif path == "/api/import":
                title = str(body.get("title", ""))
                paths = [str(value) for value in body.get("paths", [])]
                self.app.runner.start(f"导入 {title}", lambda: self.app.import_files(title, paths))
            elif path == "/api/pick-files":
                self._json({"ok": True, "paths": self.app.pick_files(bool(body.get("media_only")))})
                return
            elif path == "/api/open":
                target = self.app.open_item(str(body.get("lecture", "")), str(body.get("kind", "")))
                self._json({"ok": True, "target": target})
                return
            elif path == "/api/check-key":
                self.app.check_key()
            elif path == "/api/recorder/start":
                self.app.runner.start("启动教学网连接", self.app.start_recorder)
            elif path == "/api/recorder/login":
                username = str(body.get("username", "")).strip()
                password = str(body.get("password", ""))
                semester = str(body.get("semester", "")).strip()
                self.app.runner.start(
                    "登录教学网",
                    lambda: self.app.recorder_login_and_load(username, password, semester),
                )
            elif path == "/api/recorder/courses":
                semester = str(body.get("semester", "")).strip()
                self.app.runner.start("读取教学网课程", lambda: self.app.load_recorder_courses(semester))
            elif path == "/api/one-click":
                course_id = str(body.get("course_id", ""))
                recording_index = int(body.get("recording_index", -1))
                include_files = bool(body.get("include_files", True))
                profile = str(body.get("profile", "general"))
                self.app.schedule("one-click", {"course_id": course_id, "recording_index": recording_index, "include_files": include_files, "profile": profile, "request_limit": body.get("request_limit", 100)})
            elif path == "/api/note/save":
                lecture = str(body.get("lecture", ""))
                content = str(body.get("content", ""))
                finalize = bool(body.get("finalize", False))
                target = self.app.save_note(lecture, content, finalize)
                self._json({"ok": True, "target": target})
                return
            elif path == "/api/watch/start":
                if not body.get("offline") and not body.get("confirmed"):
                    raise PipelineError("自动处理会向 AI 服务发送新增材料并可能产生费用，请先确认。")
                self.app.watcher.start(bool(body.get("offline")), body.get("request_limit", 100))
            elif path == "/api/watch/stop":
                self.app.watcher.stop()
            else:
                self._json({"ok": False, "error": "接口不存在。"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"ok": True})
        except (PipelineError, ValueError, OSError) as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.app.events.add(traceback.format_exc(limit=4), "debug")
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def serve(course_dir: Path, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    app = WebApp(course_dir)
    handler = type("CourseNotesHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"Course Notes 可视化工作台：{url}")
    print("按 Ctrl+C 停止。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        server.server_close()


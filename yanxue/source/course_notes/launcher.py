"""Portable Windows entry point for a downloaded source release."""
from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import urllib.request
import webbrowser
from pathlib import Path

from .core import init_course, PipelineError
from .version import VERSION
from .webui import serve
from .desktop import discover_tools
from .settings import load_settings
from .records import ERROR


def prepare_workspace(project: Path, downloader: str = "") -> Path:
    settings_path = project / "local-settings.json"
    settings = load_settings(project / "workspaces" / "我的课程")
    downloader = downloader or os.environ.get("PKU_RECORDER_ROOT", "") or settings.get("downloader_root", "")
    existing = project / "workspaces" / "中级宏观经济学"
    workspace = existing if (existing / "course.toml").exists() else project / "workspaces" / "我的课程"
    if settings.get("current_course"):
        candidate = (project / "workspaces" / settings["current_course"]).resolve()
        if candidate.parent == (project / "workspaces").resolve() and (candidate / "course.toml").is_file():
            workspace = candidate
    if not (workspace / "course.toml").exists():
        if downloader and (Path(downloader) / "app.py").is_file():
            downloader = str(Path(downloader).parent)
        init_course(workspace, "我的课程", "general", downloader)
        settings["downloader_root"] = downloader
        if not settings.get(ERROR):
            settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return workspace


def main() -> None:
    discover_tools()
    parser = argparse.ArgumentParser(description="启动燕学工作台")
    default_project = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Yanxue" if getattr(sys, "frozen", False) else Path.cwd()
    parser.add_argument("--project", type=Path, default=default_project)
    parser.add_argument("--downloader", default="")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    workspace = prepare_workspace(args.project.resolve(), args.downloader)
    ports = [args.port] if args.port is not None else list(range(8765, 8776))
    if any(port < 1 or port > 65535 for port in ports):
        raise PipelineError("启动地址无效，请使用默认方式启动。")
    for port in ports:
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(url + "/api/status", timeout=.4) as response:
                status = json.load(response)
            if not isinstance(status, dict) or not status.get("ok") or "recorder" not in status:
                continue
            info = status.get("course")
            if not isinstance(info, dict) or not isinstance(info.get("path"), str) or not info["path"]:
                continue
            current = Path(info["path"]).resolve()
            if current.parent.parent != args.project.resolve():
                continue
            if status.get("version") != VERSION:
                raise PipelineError("旧版燕学仍在运行。请先完成旧版任务并关闭旧程序，再双击新版燕学；已有任务不会被自动中断。")
            if not args.no_browser:
                webbrowser.open(url)
            return
        except (OSError, ValueError):
            pass
    for port in ports:
        try:
            serve(workspace, port=port, open_browser=not args.no_browser)
            return
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE and getattr(exc, "winerror", None) != 10048:
                raise
    raise PipelineError("暂时没有可用的启动地址。请关闭之前打开的燕学，再双击桌面图标；无需手动填写网址。")


if __name__ == "__main__":
    main()

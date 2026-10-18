"""Portable Windows entry point for a downloaded source release."""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
import webbrowser
from pathlib import Path

from .core import init_course
from .webui import serve


def prepare_workspace(project: Path, downloader: str = "") -> Path:
    settings_path = project / "local-settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    downloader = downloader or os.environ.get("PKU_RECORDER_ROOT", "") or settings.get("downloader_root", "")
    existing = project / "workspaces" / "中级宏观经济学"
    workspace = existing if (existing / "course.toml").exists() else project / "workspaces" / "我的课程"
    if not (workspace / "course.toml").exists():
        if downloader and (Path(downloader) / "app.py").is_file():
            downloader = str(Path(downloader).parent)
        init_course(workspace, "我的课程", "general", downloader)
        settings_path.write_text(json.dumps({"downloader_root": downloader}, ensure_ascii=False, indent=2), encoding="utf-8")
    return workspace


def main() -> None:
    parser = argparse.ArgumentParser(description="启动燕学工作台")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--downloader", default="")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    workspace = prepare_workspace(args.project.resolve(), args.downloader)
    url = f"http://127.0.0.1:{args.port}"
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=2) as response:
            status = json.load(response)
        if status.get("ok") and "recorder" in status:
            if not args.no_browser:
                webbrowser.open(url)
            return
    except (OSError, ValueError):
        pass
    serve(workspace, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()

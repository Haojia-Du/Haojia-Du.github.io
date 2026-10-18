from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import re
from pathlib import Path

from .core import (
    CoursePipeline,
    OpenAIProvider,
    PipelineError,
    discover_downloader_materials,
    downloader_output_root,
    import_lecture,
    init_course,
    read_toml,
)


PROFILES = ["general", "stem", "economics", "humanities", "law", "language"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="course-notes", description="课程录播自动笔记流水线")
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("init", help="初始化一门课程")
    command.add_argument("path", type=Path)
    command.add_argument("--title", required=True)
    command.add_argument("--profile", choices=PROFILES, default="general")
    command.add_argument("--downloader-root", default="")

    command = subparsers.add_parser("import", help="导入一节课的材料")
    command.add_argument("course", type=Path)
    command.add_argument("--title", required=True)
    command.add_argument("sources", nargs="+", type=Path)
    command.add_argument("--move", action="store_true", help="移动文件而不是复制")

    command = subparsers.add_parser("build", help="生成一节课的笔记")
    command.add_argument("course", type=Path)
    command.add_argument("lecture", type=Path)
    command.add_argument("--offline", action="store_true")
    command.add_argument("--force", action="store_true")

    command = subparsers.add_parser("sync", help="从 PKU-Recorder 下载目录发现并导入材料")
    command.add_argument("course", type=Path)
    command.add_argument("--course-name", default="", help="只同步路径中包含该名称的文件")
    command.add_argument("--build", action="store_true", help="导入后立即构建")
    command.add_argument("--offline", action="store_true")

    command = subparsers.add_parser("watch", help="持续监听 PKU-Recorder 下载目录")
    command.add_argument("course", type=Path)
    command.add_argument("--course-name", default="")
    command.add_argument("--offline", action="store_true")
    command.add_argument("--interval", type=int, default=20)

    command = subparsers.add_parser("finalize", help="将当前初稿复制为人工定稿入口")
    command.add_argument("lecture", type=Path)

    command = subparsers.add_parser("ui", help="启动本地可视化操作界面")
    command.add_argument("course", type=Path)
    command.add_argument("--port", type=int, default=8765)
    command.add_argument("--no-browser", action="store_true")

    command = subparsers.add_parser("doctor", help="检查运行环境")
    command.add_argument("--online", action="store_true", help="联网验证 API Key")
    return parser


def sync_once(course: Path, course_name: str, build: bool, offline: bool) -> list[Path]:
    config = read_toml(course / "course.toml")
    root_value = config.get("automation", {}).get("downloader_root", "")
    if not root_value:
        raise PipelineError("course.toml 尚未配置 automation.downloader_root。")
    downloader = Path(root_value)
    filter_name = course_name or config.get("course", {}).get("title", "")
    files = discover_downloader_materials(downloader, filter_name)
    state_path = course / ".downloader-sync.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"files": {}}
    state.setdefault("files", {})
    settle_seconds = int(config.get("automation", {}).get("settle_seconds", 30))
    now = time.time()
    ready: dict[str, list[tuple[Path, str, str]]] = {}
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_size == 0 or now - stat.st_mtime < settle_seconds:
            continue
        marker = f"{stat.st_size}:{stat.st_mtime_ns}"
        key = str(path.resolve())
        if state["files"].get(key) == marker:
            continue
        date = re.search(r"20\d{2}[-_.]\d{2}[-_.]\d{2}", path.stem)
        period = re.search(r"第\d+[-—至]\d+节", path.stem)
        if date:
            title = date.group(0).replace("_", "-").replace(".", "-")
            if period:
                title += "_" + period.group(0)
        else:
            title = "课程资料"
        ready.setdefault(title, []).append((path, key, marker))
    imported: list[Path] = []
    for title, entries in ready.items():
        lecture = import_lecture(course, title, [entry[0] for entry in entries], link=True)
        for _, key, marker in entries:
            state["files"][key] = marker
        imported.append(lecture)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if build:
        pipeline = CoursePipeline(course)
        for lecture in sorted(set(imported)):
            print(f"构建：{lecture.name}")
            pipeline.build(lecture, offline=offline)
    return imported


def doctor(online: bool = False) -> int:
    checks = {
        "Python >= 3.11": sys.version_info >= (3, 11),
        "FFmpeg": shutil.which("ffmpeg") is not None,
        "FFprobe": shutil.which("ffprobe") is not None,
        "Pandoc（可选）": shutil.which("pandoc") is not None,
        "OPENAI_API_KEY（仅检测到环境变量）": bool(os.environ.get("OPENAI_API_KEY")),
    }
    try:
        import pymupdf  # noqa: F401
        checks["PyMuPDF"] = True
    except ImportError:
        try:
            import fitz  # noqa: F401
            checks["PyMuPDF"] = True
        except ImportError:
            checks["PyMuPDF"] = False
    try:
        import pptx  # noqa: F401
        checks["python-pptx"] = True
    except ImportError:
        checks["python-pptx"] = False
    for label, ok in checks.items():
        print(f"{'OK' if ok else 'MISSING'}  {label}")
    if online:
        try:
            OpenAIProvider().validate_key()
            print("OK  OpenAI API 联网验证")
        except PipelineError as exc:
            print(f"FAILED  OpenAI API 联网验证：{exc}")
            return 1
    required = [checks["Python >= 3.11"], checks["FFmpeg"], checks["FFprobe"]]
    return 0 if all(required) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            path = init_course(args.path, args.title, args.profile, args.downloader_root)
            print(f"已初始化：{path.resolve()}")
        elif args.command == "import":
            lecture = import_lecture(args.course.resolve(), args.title, args.sources, move=args.move)
            print(f"已导入：{lecture.resolve()}")
        elif args.command == "build":
            draft = CoursePipeline(args.course).build(args.lecture, offline=args.offline, force=args.force)
            print(f"已生成：{draft}")
        elif args.command == "sync":
            imported = sync_once(args.course.resolve(), args.course_name, args.build, args.offline)
            print(f"本次导入 {len(imported)} 个材料组。")
        elif args.command == "watch":
            print("正在监听下载目录；按 Ctrl+C 停止。")
            while True:
                imported = sync_once(args.course.resolve(), args.course_name, True, args.offline)
                if imported:
                    print(f"发现并处理 {len(imported)} 个新增材料组。")
                time.sleep(max(5, args.interval))
        elif args.command == "finalize":
            source = args.lecture / "generated" / "draft.md"
            if not source.exists():
                raise PipelineError(f"找不到初稿：{source}")
            target = args.lecture / "final" / "notes.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            print(f"已创建定稿入口：{target.resolve()}")
        elif args.command == "ui":
            from .webui import serve

            serve(args.course, port=args.port, open_browser=not args.no_browser)
        elif args.command == "doctor":
            return doctor(args.online)
        return 0
    except KeyboardInterrupt:
        print("已停止。")
        return 130
    except PipelineError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


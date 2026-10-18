"""Build optional Windows portable and per-user installer artifacts."""
from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def run(args: list[str], root: Path) -> None:
    subprocess.run(args, cwd=root, check=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "dist" / "windows"
    output.mkdir(parents=True, exist_ok=True)
    work = root / "build" / "windows"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
               "--name", "Yanxue", "--paths", str(root), "--distpath", str(output), "--workpath", str(work / "app"),
               "--specpath", str(work), "--add-data", str(root / "course_notes" / "web") + ";course_notes/web",
               "--collect-all", "playwright", "--collect-data", "certifi", "--hidden-import", "pypdf", "--hidden-import", "pptx",
               "--hidden-import", "flask", "--hidden-import", "flask_cors", "--hidden-import", "requests",
               "--hidden-import", "playwright.async_api", "--exclude-module", "pymupdf", "--exclude-module", "fitz",
               "--exclude-module", "PySide6", "--exclude-module", "numpy", "--exclude-module", "matplotlib",
               "--exclude-module", "tkinter", str(root / "scripts" / "windows_entry.py")]
    run(command, root)
    app = output / "Yanxue"
    licenses = app / "third-party-licenses"
    licenses.mkdir(exist_ok=True)
    for name in ("pypdf", "python-pptx", "pillow", "lxml", "flask", "flask-cors", "requests", "playwright", "certifi",
                 "urllib3", "charset-normalizer", "idna", "jinja2", "markupsafe", "werkzeug", "itsdangerous", "click", "blinker",
                 "pyee", "greenlet", "xlsxwriter", "typing-extensions"):
        dist = importlib.metadata.distribution(name)
        for item in dist.files or []:
            if any(part.lower().startswith(("license", "copying", "notice")) for part in item.parts):
                source = Path(dist.locate_file(item))
                if source.is_file():
                    target = licenses / name / Path(*item.parts[-2:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(source.read_bytes())
    (app / "使用说明.txt").write_text("双击 Yanxue.exe 启动。默认数据目录：%LOCALAPPDATA%\\Yanxue。FFmpeg/FFprobe 与浏览器组件可在页面内检查。浏览器组件按需安装；更新仅手动检查。\n", encoding="utf-8")
    archive = output / "yanxue-portable.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(output))
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onefile", "--name", "Yanxue-Setup",
         "--distpath", str(output), "--workpath", str(work / "setup"), "--specpath", str(work),
         "--add-data", str(archive) + ";payload", str(root / "scripts" / "windows_setup.py")], root)
    print(json.dumps({"portable": str(archive), "installer": str(output / "Yanxue-Setup.exe")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

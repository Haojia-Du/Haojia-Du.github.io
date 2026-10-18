"""Windows runtime diagnostics and explicitly requested component installation."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from .core import PipelineError
from .version import VERSION

DOWNLOAD_PAGE = "https://haojia-du.github.io/yanxue/"


def discover_tools() -> None:
    if os.name != "nt":
        return
    roots = [Path(sys.executable).parent / "tools", Path(os.environ.get("LOCALAPPDATA", ".")) / "Microsoft" / "WinGet" / "Links"]
    packages = Path(os.environ.get("LOCALAPPDATA", ".")) / "Microsoft" / "WinGet" / "Packages"
    if packages.exists():
        roots += [p.parent for p in packages.glob("Gyan.FFmpeg*/**/ffmpeg.exe")]
    paths = os.environ.get("PATH", "").split(os.pathsep)
    for root in roots:
        if root.is_dir() and str(root) not in paths:
            paths.insert(0, str(root))
    os.environ["PATH"] = os.pathsep.join(paths)


def browser_available() -> bool:
    base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (Path(os.environ.get("LOCALAPPDATA", ".")) / "ms-playwright"))
    try:
        import playwright
        manifest = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        revision = next(item["revision"] for item in data["browsers"] if item["name"] == "chromium")
        return any(p.is_file() for p in base.glob(f"chromium-{revision}/**/chrome.exe"))
    except (ImportError, OSError, ValueError, StopIteration):
        return False


def diagnostics() -> dict:
    discover_tools()
    return {"version": VERSION, "native": bool(getattr(sys, "frozen", False)),
            "python": sys.version.split()[0], "ffmpeg": bool(shutil.which("ffmpeg")),
            "ffprobe": bool(shutil.which("ffprobe")), "chromium": browser_available(),
            "pdf": bool(importlib.util.find_spec("pypdf") or importlib.util.find_spec("pymupdf")),
            "pptx": bool(importlib.util.find_spec("pptx")), "download_url": DOWNLOAD_PAGE}


def install_browser() -> str:
    try:
        from playwright._impl._driver import compute_driver_executable
    except ImportError:
        raise PipelineError("当前运行环境没有 Playwright，请先运行一键安装脚本或使用 Windows 启动包。") from None
    node, cli = compute_driver_executable()
    result = subprocess.run([str(node), str(cli), "install", "chromium"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=1800)
    if result.returncode:
        raise PipelineError("浏览器组件安装失败，请检查网络后重试。")
    return "Chromium 组件已安装"


def check_update() -> dict:
    request = urllib.request.Request(DOWNLOAD_PAGE + "release-manifest.json", headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        latest = str(data.get("version", ""))
        parsed = tuple(int(v) for v in latest.split("."))
        if len(parsed) != 3:
            raise ValueError("invalid version")
    except (OSError, ValueError) as exc:
        raise PipelineError("暂时无法检查更新，请稍后重试。") from None
    return {"current": VERSION, "latest": latest, "available": parsed > tuple(int(v) for v in VERSION.split(".")),
            "download_url": DOWNLOAD_PAGE, "automatic": False}


def install_ffmpeg() -> str:
    discover_tools()
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return "FFmpeg 与 FFprobe 已可用"
    winget = shutil.which("winget")
    if not winget:
        raise PipelineError("此电脑没有 winget。可把 FFmpeg/FFprobe 放在程序旁的 tools 文件夹后重新检查。")
    result = subprocess.run([winget, "install", "--id", "Gyan.FFmpeg", "--exact", "--source", "winget", "--scope", "user",
                             "--silent", "--disable-interactivity"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=1800)
    discover_tools()
    if result.returncode or not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise PipelineError("FFmpeg 安装未完成，可能需要处理系统安装提示。也可手动安装后重新检查依赖。")
    return "FFmpeg 与 FFprobe 已安装并连接"

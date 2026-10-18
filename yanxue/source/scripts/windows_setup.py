"""Optional per-user Windows installer. Each install uses a new version directory."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

VERSION = "0.3.0"


def install_to(payload: Path, destination: Path) -> Path:
    destination = destination.resolve() / (VERSION + "-" + time.strftime("%Y%m%d-%H%M%S"))
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(payload) as archive:
        for entry in archive.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination) or entry.filename.startswith(('/', '\\')):
                raise ValueError("安装包路径无效")
        archive.extractall(destination)
    executable = destination / "Yanxue" / "Yanxue.exe"
    if not executable.is_file():
        raise ValueError("安装包中缺少主程序")
    return executable


def shortcut(executable: Path, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Values are supplied as environment data rather than interpolated PowerShell code.
    environment = {**os.environ, "YANXUE_SHORTCUT_TARGET": str(executable), "YANXUE_SHORTCUT_PATH": str(path)}
    script = "$link=(New-Object -ComObject WScript.Shell).CreateShortcut($env:YANXUE_SHORTCUT_PATH); $link.TargetPath=$env:YANXUE_SHORTCUT_TARGET; $link.WorkingDirectory=[IO.Path]::GetDirectoryName($env:YANXUE_SHORTCUT_TARGET); $link.Save()"
    subprocess.run(["powershell", "-NoProfile", "-Command", script], env=environment, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), capture_output=True)


def main() -> None:
    root = tk.Tk()
    root.title("安装燕学工作台 " + VERSION)
    root.geometry("580x330")
    root.resizable(False, False)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="燕学工作台", font=("Microsoft YaHei", 20, "bold")).pack(anchor="w")
    ttk.Label(frame, text="Windows 安装版 · 无需另外安装 Python", font=("Microsoft YaHei", 10)).pack(anchor="w", pady=(5,16))
    location = tk.StringVar(value=str(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Yanxue"))
    ttk.Label(frame, text="程序安装目录（课程数据单独保存在本机用户目录）").pack(anchor="w")
    row = ttk.Frame(frame)
    row.pack(fill="x", pady=7)
    ttk.Entry(row, textvariable=location).pack(side="left", fill="x", expand=True)
    ttk.Button(row, text="选择", command=lambda: location.set(filedialog.askdirectory() or location.get())).pack(side="right", padx=(8,0))
    desktop = tk.BooleanVar(value=False)
    launch = tk.BooleanVar(value=True)
    ttk.Checkbutton(frame, text="创建桌面快捷方式（可选）", variable=desktop).pack(anchor="w", pady=4)
    ttk.Checkbutton(frame, text="安装完成后打开工作台", variable=launch).pack(anchor="w", pady=4)
    state = tk.StringVar(value="旧版本与课程数据会保留；不会自动下载更新。")
    ttk.Label(frame, textvariable=state).pack(anchor="w", pady=10)
    def begin():
        button.configure(state="disabled")
        state.set("正在安装，请稍候…")
        selected_location, make_desktop, should_launch = Path(location.get()), desktop.get(), launch.get()
        def work():
            try:
                payload = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "payload" / "yanxue-portable.zip"
                exe = install_to(payload, selected_location)
                start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "燕学工作台.lnk"
                shortcut(exe, start_menu)
                if make_desktop:
                    shortcut(exe, Path.home() / "Desktop" / "燕学工作台.lnk")
                def finish():
                    state.set("安装完成")
                    if should_launch:
                        subprocess.Popen([str(exe)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    messagebox.showinfo("安装完成", "可从开始菜单打开燕学工作台。连接设置中可按需安装浏览器组件和检查更新。")
                    root.destroy()
                root.after(0, finish)
            except Exception as exc:
                error = str(exc)
                root.after(0, lambda: (messagebox.showerror("安装未完成", error), button.configure(state="normal")))
        threading.Thread(target=work, daemon=True).start()
    button = ttk.Button(frame, text="安装", command=begin)
    button.pack(anchor="e")
    root.mainloop()


if __name__ == "__main__":
    main()

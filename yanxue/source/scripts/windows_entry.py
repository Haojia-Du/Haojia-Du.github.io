"""PyInstaller entry point, including the isolated downloader worker mode."""
import ctypes
import os
import sys
from pathlib import Path

from course_notes.desktop import discover_tools


if __name__ == "__main__":
    discover_tools()
    if len(sys.argv) > 1 and sys.argv[1] == "--recorder-bridge":
        from course_notes.recorder_bridge import main
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        main()
    else:
        from course_notes.launcher import main
        try:
            main()
        except Exception as exc:
            ctypes.windll.user32.MessageBoxW(None, str(exc), "燕学工作台启动失败", 0x10)
            raise

"""Run the frozen application with a temporary empty workspace, then stop only that process."""
import argparse
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--port", type=int, default=19876)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        process = subprocess.Popen([str(args.executable.resolve()), "--project", directory, "--port", str(args.port), "--no-browser"],
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Windows executable exited before serving")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/runtime", timeout=1) as response:
                        state = json.load(response)
                    break
                except OSError:
                    time.sleep(.2)
            else:
                raise RuntimeError("Frozen application did not start within 20 seconds")
            assert state["native"] and state["pdf"] and state["pptx"], state
            with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/") as response:
                assert "optional-features.js" in response.read().decode()
            with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/api/course/options") as response:
                assert json.load(response)["profile"] == "general"
            print("PASS: frozen Windows app serves UI, diagnostics, PDF/PPTX dependencies and course settings", flush=True)
        finally:
            process.terminate()
            process.wait(timeout=15)
        fixture = Path(directory) / "app.py"
        fixture.write_text('''from pathlib import Path
import sys
import requests
import flask_cors
from playwright.async_api import async_playwright
from flask import Flask
APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
class TestFlask(Flask):
    def run(self, *args, **kwargs):
        kwargs["port"] = 19877
        return super().run(*args, **kwargs)
app = TestFlask(__name__, root_path=str(APP_DIR))
class Manager:
    username = ""
    semester = ""
    def is_logged_in(self): return False
manager = Manager()
SETTINGS = {"app_dir": str(APP_DIR)}
def job_snapshot(): return []
def load_settings(): pass
def load_jobs(): pass
''', encoding="utf-8")
        worker = subprocess.Popen([str(args.executable.resolve()), "--recorder-bridge", str(fixture)],
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            for _ in range(100):
                if worker.poll() is not None:
                    raise RuntimeError("Frozen recorder worker exited early")
                try:
                    with urllib.request.urlopen("http://127.0.0.1:19877/api/bridge/status", timeout=1) as response:
                        status = json.load(response)
                    break
                except OSError:
                    time.sleep(.2)
            else:
                raise RuntimeError("Frozen recorder worker did not start")
            assert Path(status["settings"]["app_dir"]).resolve() == Path(directory).resolve()
            print("PASS: frozen recorder worker imports dependencies and preserves external downloader settings directory", flush=True)
        finally:
            worker.terminate()
            worker.wait(timeout=15)


if __name__ == "__main__":
    main()

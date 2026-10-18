"""Full local HTTP + FFmpeg workflow against a controlled AI fixture service."""
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from course_notes.core import init_course
from course_notes.settings import SESSION_KEYS, settings_path
from course_notes.webui import Handler, WebApp


class FixtureAI(BaseHTTPRequestHandler):
    transcriptions = 0
    reject_writer = True

    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        content = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self.send_json({"data": [{"id": "fixture-model"}]})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.endswith("/audio/transcriptions"):
            type(self).transcriptions += 1
            self.send_json({"text": "教师解释：线性模型将输入映射到输出。"})
            return
        data = json.loads(body)
        schema = data["text"]["format"]["name"]
        if schema == "course_note_section" and type(self).reject_writer:
            self.send_json({"error": "fixture credential rejected"}, 401)
            return
        values = {
            "course_note_section": {"title": "线性模型", "markdown": "## 线性模型\n\n模型 $y = wx + b$。〔来源：slides.txt｜全文〕", "key_terms": ["线性模型"], "uncertainties": [], "source_locators": ["slides.txt｜全文"]},
            "course_note_audit": {"summary": "测试材料覆盖完成", "unsupported_claims": [], "missing_topics": [], "inconsistencies": [], "manual_checks": []},
            "revision_pack": {"quiz": [{"question": "线性模型有哪些参数？", "answer": "$w$ 和 $b$。", "source": "线性模型"}], "cards": [{"question": "偏置的符号？", "answer": "$b$", "source": "线性模型"}], "outline": "## 线性模型\n解释符号与用途。"},
        }
        self.send_json({"status": "completed", "output_text": json.dumps(values[schema], ensure_ascii=False)})


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg and FFprobe")
class EndToEndTests(unittest.TestCase):
    def test_import_transcribe_fail_retry_notes_review_and_media(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            course = init_course(root / "workspaces" / "course", "验收课程", "general")
            slides = root / "slides.txt"
            slides.write_text("线性模型：y=wx+b，其中w为权重，b为偏置。", encoding="utf-8")
            audio = root / "lecture.wav"
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(audio)], check=True, capture_output=True)
            FixtureAI.transcriptions, FixtureAI.reject_writer = 0, True
            ai = ThreadingHTTPServer(("127.0.0.1", 0), FixtureAI)
            app = WebApp(course)
            ui = ThreadingHTTPServer(("127.0.0.1", 0), type("AcceptanceHandler", (Handler,), {"app": app}))
            threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (ai, ui)]
            for thread in threads:
                thread.start()

            def call(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                request = urllib.request.Request(f"http://127.0.0.1:{ui.server_port}" + path, data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    return json.load(response)

            def wait_job():
                for _ in range(200):
                    if not app.runner.running:
                        return
                    time.sleep(.025)
                self.fail("background job did not finish")

            try:
                call("/api/settings", {"api_key": "fixture-key", "base_url": f"http://127.0.0.1:{ai.server_port}/v1", "writer": "fixture", "reviewer": "fixture", "transcriber": "fixture"})
                self.assertTrue(call("/api/check-key", {})["ok"])
                call("/api/import", {"title": "lesson", "paths": [str(slides), str(audio)]})
                wait_job()
                call("/api/build", {"lecture": "lesson", "offline": False})
                wait_job()
                self.assertIn("401", app.runner.error)
                self.assertTrue(app.runner.status()["can_retry"])
                app = WebApp(course)
                ui.RequestHandlerClass.app = app
                self.assertTrue(app.runner.status()["can_retry"], "failed task must survive a workbench restart")
                FixtureAI.reject_writer = False
                call("/api/retry", {})
                wait_job()
                self.assertEqual(app.runner.error, "")
                self.assertEqual(FixtureAI.transcriptions, 1, "retry must reuse successful transcription")
                note = call("/api/note?lecture=lesson&kind=draft")
                self.assertIn("〔来源：", note["content"])
                self.assertEqual(len(call("/api/materials?lecture=lesson")["files"]), 2)
                call("/api/review/generate", {"lecture": "lesson"})
                wait_job()
                review = call("/api/review?lecture=lesson")
                self.assertTrue(review["available"])
                self.assertFalse(review["stale"])
                call("/api/review/progress", {"lecture": "lesson", "index": 0, "rating": "known"})
                self.assertEqual(len(call("/api/review?lecture=lesson")["progress"]), 1)
                call("/api/note/save", {"lecture": "lesson", "content": note["content"] + "\n修订", "finalize": True})
                self.assertTrue(call("/api/review?lecture=lesson")["stale"])
                self.assertNotIn("fixture-key", json.dumps(call("/api/settings")))
            finally:
                for server in (ai, ui):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join()
                SESSION_KEYS.pop(str(settings_path(course)), None)


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from course_notes.cli import sync_once
from course_notes.core import CoursePipeline, PipelineError, init_course, import_lecture, atomic_write
from course_notes.settings import save_settings, api_key, settings_path, SESSION_KEYS
from course_notes.study import generate_review
from course_notes.webui import WebApp, BackgroundRunner, EventLog, Handler


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.course = init_course(self.root / "workspaces" / "test", "测试课程", "general", str(self.root / "recorder"))

    def tearDown(self):
        SESSION_KEYS.pop(str(settings_path(self.course)), None)
        self.tmp.cleanup()

    def test_sync_failure_does_not_acknowledge_material(self):
        source = self.root / "2026-01-01.txt"
        source.write_text("课程内容", encoding="utf-8")
        config = (self.course / "course.toml").read_text(encoding="utf-8").replace("settle_seconds = 30", "settle_seconds = 0")
        atomic_write(self.course / "course.toml", config)
        with patch("course_notes.cli.discover_downloader_materials", return_value=[source]), patch("course_notes.cli.CoursePipeline") as cls:
            cls.return_value.build.side_effect = [PipelineError("temporary"), None]
            with self.assertRaises(PipelineError):
                sync_once(self.course, "测试", True, False)
            self.assertFalse((self.course / ".downloader-sync.json").exists())
            result = sync_once(self.course, "测试", True, False)
            self.assertEqual(len(result), 1)
            self.assertEqual(cls.return_value.build.call_count, 2)

    def test_transcription_resumes_completed_chunks(self):
        source = self.root / "lecture.mp4"
        source.write_bytes(b"fixture")
        lecture = import_lecture(self.course, "lecture", [source])
        chunks = [self.root / "chunk_0.mp3", self.root / "chunk_1.mp3"]
        for chunk in chunks:
            chunk.write_bytes(b"fixture")
        provider = MagicMock()
        provider.transcribe.side_effect = [{"text": "first"}, PipelineError("network"), {"text": "second"}]
        provider.structured_response.side_effect = lambda **kwargs: {"markdown": "## 笔记\n课程正文"} if kwargs["schema_name"] == "course_note_section" else {"summary": "通过"}
        with patch("course_notes.core.OpenAIProvider", return_value=provider), patch("course_notes.core.split_audio", return_value=chunks), patch("course_notes.core.media_duration", return_value=12):
            with self.assertRaises(PipelineError):
                CoursePipeline(self.course).build(lecture)
            CoursePipeline(self.course).build(lecture)
        self.assertEqual(provider.transcribe.call_count, 3)
        self.assertEqual(provider.transcribe.call_args_list[-1].args[0], chunks[1])

    def test_settings_mask_key_and_use_session_only(self):
        key = "unit-test-secret-sentinel"
        save_settings(self.course, {"api_key": key, "base_url": "https://example.com/v1"})
        self.assertEqual(api_key(self.course), key)
        self.assertNotIn(key, settings_path(self.course).read_text(encoding="utf-8"))
        self.assertNotIn(key, json.dumps(WebApp(self.course).settings_data()))
        self.assertEqual(CoursePipeline(self.course).config["models"]["base_url"], "https://example.com/v1")

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI")
    def test_remembered_key_is_encrypted_and_survives_session(self):
        key = "unit-test-dpapi-sentinel"
        save_settings(self.course, {"api_key": key, "remember_key": True})
        SESSION_KEYS.pop(str(settings_path(self.course)))
        self.assertNotIn(key, settings_path(self.course).read_text(encoding="utf-8"))
        self.assertEqual(api_key(self.course), key)

    def test_interrupted_task_restores_retry_descriptor(self):
        path = self.root / "local-task.json"
        path.write_text(json.dumps({"state": "running", "kind": "build", "args": {"lecture": "x"}}), encoding="utf-8")
        runner = BackgroundRunner(EventLog(), path)
        self.assertTrue(runner.status()["can_retry"])
        self.assertIn("中断", runner.status()["error"])

    def test_recording_retry_does_not_redownload(self):
        app = WebApp(self.course)
        recorder = self.root / "recording.mp4"
        recorder.write_bytes(b"fixture")
        snapshot = {"course_id": "c1", "name": "下载课程", "recordings": [{"name": "第一讲", "time": "2026-01-01"}]}
        job = {"id": "j1", "status": "done", "course_id": "c1", "file": str(recorder), "rec_name": "第一讲", "progress": 100}
        app.recorder_status = MagicMock(return_value={"running": True})
        app._recorder_api = MagicMock(side_effect=[{"jobs": []}, {"ok": True}, {"jobs": [job]}, {"jobs": [job], "settings": {}}])
        with patch("course_notes.workflow.CoursePipeline") as cls:
            cls.return_value.build.side_effect = [PipelineError("bad key"), None]
            with self.assertRaises(PipelineError):
                app.one_click_process("c1", 0, False, "general", snapshot)
            app.one_click_process("c1", 0, False, "general", snapshot)
        calls = [call for call in app._recorder_api.call_args_list if call.args[0] == "/api/download"]
        self.assertEqual(len(calls), 1)

    def test_review_is_cached_and_invalidated_on_note_edit(self):
        lecture = self.course / "lectures" / "one"
        atomic_write(lecture / "final" / "notes.md", "## 概念\n课程论点")
        value = {"quiz": [{"question": "什么是概念？", "answer": "论点", "source": "概念"}], "cards": [], "outline": "## 概念"}
        with patch("course_notes.study.OpenAIProvider") as cls:
            cls.return_value.structured_response.return_value = value
            generate_review(self.course, lecture)
            generate_review(self.course, lecture)
            self.assertEqual(cls.return_value.structured_response.call_count, 1)
            atomic_write(lecture / "final" / "notes.md", "## 概念\n修订论点")
            generate_review(self.course, lecture)
            self.assertEqual(cls.return_value.structured_response.call_count, 2)

    def test_material_endpoint_supports_range_and_rejects_traversal(self):
        source = self.root / "clip.mp4"
        source.write_bytes(b"0123456789")
        lecture = import_lecture(self.course, "clip", [source])
        app = WebApp(self.course)
        server = ThreadingHTTPServer(("127.0.0.1", 0), type("TestHandler", (Handler,), {"app": app}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            request = urllib.request.Request(url + "/api/material?lecture=clip&index=0", headers={"Range": "bytes=2-5"})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b"2345")
                self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url + "/api/material?lecture=../&index=0")
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()

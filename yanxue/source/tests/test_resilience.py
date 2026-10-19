import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from course_notes.audit import audit_sections
from course_notes.batch import batch_status, prepare_batch
from course_notes.core import Evidence, PipelineError, init_course, write_json
from course_notes.diagnostics import diagnostic_report, failure_info
from course_notes.launcher import prepare_workspace
from course_notes.records import read_record, ERROR
from course_notes.settings import effective_config, save_settings, SESSION_KEYS, settings_path
from course_notes.webui import BackgroundRunner, EventLog, Handler, WebApp
from test_validation import QA


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.course = init_course(self.root / "workspaces" / "course", "私密课程", "general")
        self.app = WebApp(self.course)

    def tearDown(self):
        self.tmp.cleanup()

    def test_audit_covers_every_batch_and_long_note_without_truncation(self):
        provider = MagicMock()
        provider.structured_response.return_value = QA
        groups = [[Evidence("slides", str(i), "证据"*9000, "text")] for i in range(4)]
        sections = [{"markdown":"笔记"*12000 + f"末尾-{i}"} for i in range(4)]
        result, coverage = audit_sections(provider, groups, sections, "model", "http://fixture", self.root / "audit")
        self.assertEqual(provider.structured_response.call_count, 8)
        self.assertEqual(coverage["note_characters"], sum(len(s["markdown"]) for s in sections))
        self.assertIn("末尾-3", provider.structured_response.call_args.kwargs["user_input"])
        self.assertEqual(coverage["batches"], 4)
        self.assertIn("未进行跨批次", result["summary"])

    def test_audit_resume_reuses_only_completed_checkpoints(self):
        provider = MagicMock()
        provider.structured_response.side_effect = [QA, PipelineError("network"), QA]
        groups = [[Evidence("x", str(i), "内容", "text")] for i in range(2)]
        sections = [{"markdown":"第一段"}, {"markdown":"第二段"}]
        with self.assertRaises(PipelineError):
            audit_sections(provider, groups, sections, "m", "url", self.root / "audit")
        audit_sections(provider, groups, sections, "m", "url", self.root / "audit")
        self.assertEqual(provider.structured_response.call_count, 3)

    def test_audit_batch_mismatch_is_not_silently_ignored(self):
        with self.assertRaisesRegex(PipelineError, "数量不符"):
            audit_sections(MagicMock(), [[Evidence("x", "1", "内容", "text")]], [], "m", "url", self.root / "audit")

    def test_invalid_course_option_fields_are_disabled_and_backed_up(self):
        from course_notes.settings import save_course_options
        path = self.course / "course-options.json"
        original = {"profile":{}, "terms":5}
        write_json(path, original)
        self.assertTrue(self.app.config["_settings_error"])
        self.assertEqual(self.app.config["_api_key"], "")
        save_course_options(self.course, {"profile":"general", "terms":[]})
        backups = list(self.course.glob("course-options.json.corrupt-*.bak"))
        self.assertEqual(json.loads(backups[0].read_text()), original)

    def test_damaged_course_toml_does_not_break_workspace_listing(self):
        path = self.course / "course.toml"
        path.write_text("[broken", encoding="utf-8")
        self.assertEqual(self.app.config["_api_key"], "")
        self.assertIn("course.toml", self.app.config["_settings_error"])
        self.assertEqual(len(self.app.local_courses()), 1)
        self.assertEqual(path.read_text(), "[broken")

    def test_wrong_toml_section_type_is_not_used_for_ai(self):
        (self.course / "course.toml").write_text('models = "bad"', encoding="utf-8")
        self.assertEqual(self.app.config["_api_key"], "")
        self.assertTrue(self.app.config["_settings_error"])

    def test_failed_draft_cannot_generate_review_pack(self):
        from course_notes.study import generate_review
        lecture = self.course / "lectures" / "draft"
        write_json(lecture / "generated" / "validation.json", {"status":"failed"})
        (lecture / "generated" / "draft.md").write_text("不完整笔记", encoding="utf-8")
        with self.assertRaisesRegex(PipelineError, "尚未通过"):
            generate_review(self.course, lecture)

    def test_corrupt_settings_do_not_enable_env_or_session_credentials(self):
        path = settings_path(self.course)
        path.write_text("broken", encoding="utf-8")
        SESSION_KEYS[str(path)] = "session-sentinel"
        with patch.dict(os.environ, {"OPENAI_API_KEY":"env-sentinel"}):
            config = self.app.config
        self.assertEqual(config["_api_key"], "")
        self.assertTrue(config["_settings_error"])
        self.assertEqual(path.read_text(), "broken")
        prepare_workspace(self.root)
        self.assertEqual(path.read_text(), "broken")

    def test_explicit_settings_save_backs_up_corrupt_original(self):
        path = settings_path(self.course)
        path.write_text("broken", encoding="utf-8")
        save_settings(self.course, {"api_key":"test", "base_url":"https://example.com/v1"})
        backups = list(self.root.glob("local-settings.json.corrupt-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "broken")
        self.assertNotIn(ERROR, read_record(path))

    def test_corrupt_task_is_preserved_until_explicit_restart(self):
        path = self.root / "local-task.json"
        path.write_text("[]", encoding="utf-8")
        runner = BackgroundRunner(EventLog(), path)
        self.assertIn("损坏", runner.error)
        self.assertFalse(runner.status()["can_retry"])
        self.assertEqual(path.read_text(), "[]")
        done = threading.Event()
        runner.start("test", done.set)
        self.assertTrue(done.wait(2))
        import time
        deadline = time.monotonic() + 2
        while runner.running and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(runner.running)
        self.assertEqual(len(list(self.root.glob("local-task.json.corrupt-*.bak"))), 1)

    def test_corrupt_batch_does_not_break_status_and_is_backed_up(self):
        path = self.course / ".batch-progress.json"
        path.write_text("[]", encoding="utf-8")
        self.assertFalse(batch_status(self.app)["available"])
        course = {"name":"私密课程", "course_id":"c", "recordings":[{"name":"一讲"}]}
        self.app._activate_course = MagicMock()
        prepare_batch(self.app, course, [0], "general", False, False)
        self.assertEqual(len(list(self.course.glob(".batch-progress.json.corrupt-*.bak"))), 1)

    def test_failed_task_persist_does_not_leave_runner_busy(self):
        runner = BackgroundRunner(EventLog(), self.root / "task.json")
        with patch("course_notes.webui.write_json", side_effect=OSError("disk full")):
            with self.assertRaises(PipelineError):
                runner.start("test", lambda: None)
        self.assertFalse(runner.running)

    def test_watch_initialization_failure_is_visible(self):
        with patch("course_notes.safety.write_json", side_effect=OSError("disk full")):
            self.app.watcher.start(False, 10)
            self.app.watcher._thread.join(2)
        self.assertFalse(self.app.watcher.running)
        self.assertIn("disk full", self.app.watcher.error)

    def test_diagnostics_allowlist_excludes_sensitive_values(self):
        self.app.runner.error = "401 secret-sentinel " + str(self.course)
        report = json.dumps(diagnostic_report(self.app), ensure_ascii=False)
        for value in ["secret-sentinel", str(self.course), "私密课程", "local-settings"]:
            self.assertNotIn(value, report)
        self.assertEqual(failure_info(self.app.runner.error)["code"], "AI_AUTH")

    def test_http_rejects_rebinding_host_and_array_body(self):
        handler = type("TestHandler", (Handler,), {"app":self.app})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            request = urllib.request.Request(url + "/api/status", headers={"Host":"attacker.invalid"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            request = urllib.request.Request(url + "/api/sample", data=b"[]", headers={"Content-Type":"application/json"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            with urllib.request.urlopen(url + "/api/diagnostics") as response:
                self.assertTrue(json.load(response)["ok"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

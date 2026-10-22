import errno
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from course_notes.core import PipelineError, init_course
from course_notes.launcher import main, VERSION
from course_notes.settings import save_settings, settings_path, SESSION_KEYS
from course_notes.webui import WebApp, Handler
from test_end_to_end import FixtureAI


class SimpleFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.course = init_course(self.root / "workspaces" / "course", "课程", "general")
        self.app = WebApp(self.course)
        save_settings(self.course, {"clear_key":True})
        self.source = self.root / "slides.txt"
        self.source.write_text("线性模型将特征映射到预测结果。", encoding="utf-8")

    def tearDown(self):
        SESSION_KEYS.pop(str(settings_path(self.course)), None)
        self.tmp.cleanup()

    def test_offline_single_action_imports_and_extracts(self):
        args = {"paths":[str(self.source)], "offline":True, "title":""}
        self.app.simple_process(args)
        self.assertEqual(args["title"], "slides")
        note = self.course / "lectures" / "slides" / "generated" / "draft.md"
        self.assertIn("线性模型", note.read_text(encoding="utf-8"))
        self.assertEqual(self.app.list_lectures()[0]["status"], "offline")

    def test_key_missing_does_not_import_or_call_ai(self):
        with self.assertRaisesRegex(PipelineError, "连接 AI"):
            self.app.validate_simple({"paths":[str(self.source)], "offline":False})
        self.assertEqual(self.app.list_lectures(), [])

    def test_invalid_input_is_rejected_before_scheduling(self):
        for args in [{"paths":"bad"}, {"paths":[]}, {"paths":[str(self.root / "missing.mp4")], "offline":True}, {"paths":[str(self.source)], "offline":"false"}]:
            with self.subTest(args=args), self.assertRaises(PipelineError):
                self.app.validate_simple(args)

    def test_single_online_action_builds_and_is_guarded(self):
        ai = ThreadingHTTPServer(("127.0.0.1", 0), FixtureAI)
        worker = threading.Thread(target=ai.serve_forever, daemon=True)
        worker.start()
        FixtureAI.reject_writer = False
        try:
            save_settings(self.course, {"api_key":"fixture", "base_url":f"http://127.0.0.1:{ai.server_port}/v1", "writer":"fixture", "reviewer":"fixture"})
            with patch.object(self.app.runner, "start") as start:
                self.app.schedule("simple", {"paths":[str(self.source)], "offline":False, "request_limit":5})
                self.assertEqual(start.call_args.args[2]["kind"], "simple")
                start.call_args.args[1]()
            note = self.course / "lectures" / "slides" / "generated" / "draft.md"
            self.assertIn("线性模型", note.read_text(encoding="utf-8"))
            usage = json.loads((self.root / "request-usage.json").read_text())
            self.assertEqual(usage, {"used":2, "limit":5})
        finally:
            ai.shutdown(); ai.server_close(); worker.join(2)

    def test_paid_endpoint_requires_explicit_confirmation(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), type("TestHandler", (Handler,), {"app":self.app}))
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        try:
            payload = json.dumps({"paths":[str(self.source)], "offline":False}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/simple/start", data=payload, headers={"Content-Type":"application/json"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            self.assertFalse(self.app.runner.running)
        finally:
            server.shutdown(); server.server_close(); worker.join(2)

    def test_launcher_chooses_available_address_without_user_port_choice(self):
        with patch("sys.argv", ["yanxue", "--project", str(self.root), "--no-browser"]), patch("course_notes.launcher.urllib.request.urlopen", side_effect=urllib.error.URLError("not running")), patch("course_notes.launcher.serve", side_effect=[OSError(errno.EADDRINUSE, "busy"), None]) as serve:
            main()
        self.assertEqual([call.kwargs["port"] for call in serve.call_args_list], [8765, 8766])

    def test_launcher_reuses_existing_workspace_and_opens_its_page(self):
        state = {"ok":True, "version":VERSION, "recorder":{}, "course":{"path":str(self.course)}}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(state).encode()
        def probe(url, **kwargs):
            if ":8766/" in url: return response
            raise urllib.error.URLError("not running")
        with patch("sys.argv", ["yanxue", "--project", str(self.root)]), patch("course_notes.launcher.urllib.request.urlopen", side_effect=probe), patch("course_notes.launcher.webbrowser.open") as browser, patch("course_notes.launcher.serve") as serve:
            main()
        browser.assert_called_once_with("http://127.0.0.1:8766")
        serve.assert_not_called()

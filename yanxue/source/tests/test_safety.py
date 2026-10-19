import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from course_notes.core import PipelineError, OpenAIProvider
from course_notes.safety import request_limit, before_request, sample_lesson
import test_optional as helpers
from course_notes.batch import edit_batch, prepare_batch, run_batch, batch_status


class RequestLimitTests(unittest.TestCase):
    def test_stop_prevents_further_requests(self):
        import threading
        stop = threading.Event()
        with tempfile.TemporaryDirectory() as folder, request_limit(Path(folder) / "usage.json", 10, stop):
            before_request()
            stop.set()
            with self.assertRaisesRegex(PipelineError, "已停止"):
                before_request()

    def test_limit_blocks_before_network_and_resets_context(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.json"
            with request_limit(path, 1):
                before_request()
                with self.assertRaises(PipelineError):
                    before_request()
            before_request()
            self.assertEqual(json.loads(path.read_text())["used"], 1)

    def test_retry_attempts_count(self):
        import urllib.error
        with tempfile.TemporaryDirectory() as folder:
            provider = OpenAIProvider("test", "http://localhost")
            with request_limit(Path(folder) / "usage.json", 1), patch("course_notes.core.time.sleep"), patch("course_notes.core.urllib.request.urlopen", side_effect=urllib.error.HTTPError("test", 429, "limit", {}, None)) as network:
                with self.assertRaisesRegex(PipelineError, "次数上限"):
                    provider.validate_key()
                self.assertEqual(network.call_count, 1)


class BatchControlTests(unittest.TestCase):
    setUp = helpers.OptionalFeatureTests.setUp
    tearDown = helpers.OptionalFeatureTests.tearDown
    fake_lesson = helpers.OptionalFeatureTests.fake_lesson

    def test_watch_limit_is_shared_across_poll_cycles(self):
        def sync(*args, **kwargs):
            before_request()
            return []
        with patch("course_notes.webui.sync_once", side_effect=sync) as sync_call, patch.object(self.app.watcher._stop, "wait", return_value=False):
            self.app.watcher.start(False, 2)
            self.app.watcher._thread.join(timeout=3)
            self.assertFalse(self.app.watcher.running)
            self.assertEqual(sync_call.call_count, 3)
            self.assertIn("次数上限", self.app.watcher.error)
            usage = json.loads((self.root / "request-usage.json").read_text())
            self.assertEqual(usage["used"], 2)

    def test_watch_failure_stops_without_automatic_retry(self):
        with patch("course_notes.webui.sync_once", side_effect=PipelineError("network failed")) as sync_call:
            self.app.watcher.start(False, 10)
            self.app.watcher._thread.join(timeout=3)
            self.assertFalse(self.app.watcher.running)
            self.assertEqual(sync_call.call_count, 1)
            self.assertEqual(self.app.watcher.error, "network failed")

    def test_media_picker_is_single_select_and_unicode_safe(self):
        response = MagicMock(returncode=0, stdout='"C:\\\\课程\\\\录播.mp4"', stderr="")
        with patch("course_notes.webui.subprocess.run", return_value=response) as run:
            self.assertEqual(self.app.pick_files(True), ["C:\\课程\\录播.mp4"])
            script = run.call_args.args[0][-1]
            self.assertIn("Multiselect = $false", script)
            self.assertIn("OutputEncoding", script)
            self.assertNotIn("*.pdf", script)

    def test_sample_clips_media_and_reuses_on_retry(self):
        import shutil
        from course_notes.core import run_checked, media_duration
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("requires FFmpeg and FFprobe")
        source = self.root / "short.wav"
        run_checked(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(source)])
        fake = MagicMock()
        fake.course_dir = self.course
        fake.config = {"_api_key": "test"}
        with patch("course_notes.core.CoursePipeline") as pipeline:
            sample_lesson(fake, str(source), "test-sample")
            clip = self.course / "samples" / "test-sample" / "前3分钟.mp3"
            self.assertLess(media_duration(clip), 3)
            timestamp = clip.stat().st_mtime_ns
            sample_lesson(fake, str(source), "test-sample")
            self.assertEqual(clip.stat().st_mtime_ns, timestamp)
            self.assertEqual(pipeline.return_value.build.call_count, 2)
    def test_cancel_keeps_results_and_allows_new_batch(self):
        prepare_batch(self.app, self.snapshot, [0, 1], "general", False, False)
        edit_batch(self.app, "cancel")
        self.assertEqual(batch_status(self.app)["state"], "canceled")
        prepare_batch(self.app, self.snapshot, [0], "general", False, False)

    def test_skip_failure_and_continue(self):
        task = prepare_batch(self.app, self.snapshot, [0, 1], "general", False, False)
        self.app.one_click_process = MagicMock(side_effect=PipelineError("failed"))
        with self.assertRaises(PipelineError):
            run_batch(self.app, task["batch_id"])
        edit_batch(self.app, "skip")
        self.app.one_click_process = MagicMock(side_effect=self.fake_lesson)
        run_batch(self.app, task["batch_id"])
        self.assertEqual(self.calls, [1])
        self.assertEqual(batch_status(self.app)["items"][0]["state"], "skipped")

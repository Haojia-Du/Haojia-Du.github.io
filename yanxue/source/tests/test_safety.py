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

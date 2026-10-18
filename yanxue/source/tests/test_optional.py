import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from course_notes.batch import prepare_batch, run_batch, batch_status
from course_notes.core import init_course, import_lecture, PipelineError, write_json
from course_notes.settings import effective_config, save_course_options
from course_notes.webui import WebApp
from course_notes.workflow import recording_identity
from course_notes.desktop import check_update


class OptionalFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.course = init_course(self.root / "workspaces" / "start", "初始课程", "general")
        self.app = WebApp(self.course)
        self.snapshot = {"course_id": "c1", "name": "批量测试课程", "recordings": [
            {"name": "第一讲", "time": "2026-01-01"}, {"name": "第二讲", "time": "2026-01-02"}]}
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_lesson(self, course_id, index, include, profile, snapshot, additional_sources):
        self.calls.append(index)
        name = f"lesson-{index}"
        source = self.root / "slides.txt"
        source.write_text("共同课件", encoding="utf-8")
        lecture = import_lecture(self.app.course_dir, name, [source])
        identity = recording_identity(course_id, snapshot["recordings"][index], include)
        write_json(self.app.course_dir / (".recording-" + identity + ".json"), {"lecture": lecture.name})
        return str(lecture)

    def test_batch_failure_resume_skips_completed_lessons(self):
        task = prepare_batch(self.app, self.snapshot, [0,1], "humanities", True, True)
        def run(*args):
            if args[1] == 1 and self.calls == [0]:
                self.calls.append(1)
                raise PipelineError("retry")
            return self.fake_lesson(*args)
        self.app.one_click_process = MagicMock(side_effect=run)
        with patch("course_notes.batch.generate_review") as review:
            with self.assertRaises(PipelineError):
                run_batch(self.app, task["batch_id"])
            state = batch_status(self.app)
            self.assertEqual(state["items"][0]["state"], "done")
            self.assertEqual(state["items"][1]["state"], "error")
            run_batch(self.app, task["batch_id"])
            self.assertEqual(self.calls, [0,1,1])
            self.assertEqual(review.call_count, 2)
        self.assertEqual(batch_status(self.app)["state"], "done")
        self.assertEqual(self.app.config["course"]["profile"], "humanities")

    def test_pause_between_lessons_and_restart(self):
        task = prepare_batch(self.app, self.snapshot, [0,1], "general", False, False)
        def run(*args):
            result = self.fake_lesson(*args)
            self.app.batch_pause.set()
            return result
        self.app.one_click_process = MagicMock(side_effect=run)
        run_batch(self.app, task["batch_id"])
        self.assertEqual(batch_status(self.app)["state"], "paused")
        course = self.app.course_dir
        self.app = WebApp(course)
        self.app.one_click_process = MagicMock(side_effect=self.fake_lesson)
        run_batch(self.app, task["batch_id"])
        self.assertEqual(self.calls, [0,1])
        self.assertEqual(batch_status(self.app)["state"], "done")

    def test_course_options_are_isolated(self):
        other = init_course(self.root / "workspaces" / "other", "另一门课", "general")
        save_course_options(self.course, {"profile":"language", "terms":["IPA", "IPA"], "prompt":"保留语境"})
        from course_notes.core import read_toml
        options = effective_config(self.course, read_toml(self.course / "course.toml"))
        self.assertEqual(options["course"]["terms"], ["IPA"])
        self.assertEqual(options["course"]["profile"], "language")
        self.assertEqual(effective_config(other, read_toml(other / "course.toml"))["course"]["profile"], "general")
        self.app.select_course("other")
        from course_notes.launcher import prepare_workspace
        self.assertEqual(prepare_workspace(self.root), other.resolve())

    def test_update_check_never_downloads_installer(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"version":"9.0.0"}'
        with patch("course_notes.desktop.urllib.request.urlopen", return_value=response) as open_request:
            result = check_update()
        self.assertTrue(result["available"])
        self.assertFalse(result["automatic"])
        self.assertEqual(open_request.call_count, 1)


if __name__ == "__main__":
    unittest.main()

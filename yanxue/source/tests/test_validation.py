import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from course_notes.core import CoursePipeline, NOTE_SCHEMA, QA_SCHEMA, PipelineError, init_course, import_lecture, write_json
from course_notes.validation import validate_schema, validate_transcription, validate_transcript, cached_result
from course_notes.study import generate_review


NOTE = {"title":"测试", "markdown":"## 内容\n有据可查。〔来源：材料.txt｜全文〕", "key_terms":[], "uncertainties":[], "source_locators":["材料.txt｜全文"]}
QA = {"summary":"测试质检", "unsupported_claims":[], "missing_topics":[], "inconsistencies":[], "manual_checks":[]}


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.course = init_course(self.root / "course", "检验课程", "general")
        self.source = self.root / "材料.txt"
        self.source.write_text("有效课程材料。", encoding="utf-8")
        self.lecture = import_lecture(self.course, "第一讲", [self.source])

    def tearDown(self):
        self.tmp.cleanup()

    def provider(self):
        result = MagicMock()
        result.structured_response.side_effect = lambda **kw: dict(NOTE if kw["schema_name"] == "course_note_section" else QA)
        return result

    def test_reject_empty_and_invalid_structured_outputs(self):
        for value in [[], {}, {**NOTE, "markdown":"  "}, {**NOTE, "key_terms":"bad"}]:
            with self.subTest(value=value), self.assertRaises(PipelineError):
                validate_schema(value, NOTE_SCHEMA)
        for value in [None, {}, {"text":"  "}, {"text":5}]:
            with self.assertRaises(PipelineError):
                validate_transcription(value)

    def test_detect_transcript_gaps_and_truncation(self):
        valid = {"chunks":[{"start":0,"end":10,"text":"内容"}]}
        validate_transcript(valid, 10)
        for value, duration in [({"chunks":[]}, 10), (valid, 60), ({"chunks":[{"start":5,"end":10,"text":"内容"}]},10)]:
            with self.assertRaises(PipelineError):
                validate_transcript(value, duration)

    def test_invalid_cache_is_not_reused_or_deleted(self):
        path = self.root / "cache.json"
        path.write_text("broken", encoding="utf-8")
        self.assertIsNone(cached_result(path, validate_transcription))
        self.assertEqual(path.read_text(), "broken")

    def test_empty_material_is_failed_not_completed(self):
        (self.lecture / "source" / "材料.txt").write_text("", encoding="utf-8")
        with self.assertRaises(PipelineError):
            CoursePipeline(self.course).build(self.lecture, offline=True)
        report = json.loads((self.lecture / "generated" / "validation.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "failed")
        self.assertFalse((self.lecture / ".course-notes-state.json").exists())

    def test_missing_external_material_is_not_silently_ignored(self):
        write_json(self.lecture / "source" / ".external-sources.json", [str(self.root / "missing.pdf")])
        with self.assertRaisesRegex(PipelineError, "丢失"):
            CoursePipeline(self.course).build(self.lecture, offline=True)

    def test_broken_artifacts_and_section_cache_are_repaired(self):
        provider = self.provider()
        with patch("course_notes.core.OpenAIProvider", return_value=provider):
            pipeline = CoursePipeline(self.course)
            draft = pipeline.build(self.lecture)
            pipeline.build(self.lecture)
            self.assertEqual(provider.structured_response.call_count, 2)
            draft.write_text("", encoding="utf-8")
            cache = next((self.lecture / "generated" / "sections").glob("*.json"))
            cache.write_text("{}", encoding="utf-8")
            pipeline.build(self.lecture)
            self.assertEqual(provider.structured_response.call_count, 3)  # Valid audit checkpoint is reused.
            self.assertIn("有据可查", draft.read_text(encoding="utf-8"))

    def test_audit_failure_has_stage_and_never_marks_success(self):
        provider = self.provider()
        provider.structured_response.side_effect = [NOTE, {"summary":"missing fields"}]
        with patch("course_notes.core.OpenAIProvider", return_value=provider), self.assertRaises(PipelineError):
            CoursePipeline(self.course).build(self.lecture)
        report = json.loads((self.lecture / "generated" / "validation.json").read_text(encoding="utf-8"))
        self.assertEqual(report["stage"], "语义质检")
        self.assertFalse((self.lecture / ".course-notes-state.json").exists())

    def test_empty_review_is_not_saved(self):
        final = self.lecture / "final" / "notes.md"
        final.parent.mkdir(parents=True)
        final.write_text("课程笔记", encoding="utf-8")
        with patch("course_notes.study.OpenAIProvider") as provider:
            provider.return_value.structured_response.return_value = {"quiz":[], "cards":[], "outline":"提纲"}
            with self.assertRaises(PipelineError):
                generate_review(self.course, self.lecture)
        self.assertFalse((self.lecture / "generated" / "review.json").exists())

    def test_warning_is_visible_and_not_a_semantic_pass(self):
        provider = self.provider()
        provider.structured_response.side_effect = [NOTE, {**QA, "missing_topics":["需核对"]}]
        with patch("course_notes.core.OpenAIProvider", return_value=provider):
            CoursePipeline(self.course).build(self.lecture)
        from course_notes.webui import WebApp
        report = WebApp(self.course).list_lectures()[0]["validation"]
        self.assertEqual(report["status"], "warning")
        self.assertTrue(report["warnings"])

    def test_failed_draft_cannot_be_directly_finalized(self):
        from course_notes.webui import WebApp
        write_json(self.lecture / "generated" / "validation.json", {"status":"failed"})
        (self.lecture / "generated" / "draft.md").write_text("不完整笔记", encoding="utf-8")
        with self.assertRaisesRegex(PipelineError, "未通过"):
            WebApp(self.course).finalize(self.lecture.name)

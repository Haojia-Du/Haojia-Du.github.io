import json
import tempfile
import unittest
from pathlib import Path

from course_notes.core import Evidence, discover_downloader_materials, extract_text_file, make_chunks, slugify


class CoreTests(unittest.TestCase):
    def test_page_markers_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "slides.txt"
            path.write_text("===== PAGE 1 =====\n第一节\n===== PAGE 2 =====\n第二节", encoding="utf-8")
            evidence = extract_text_file(path)
        self.assertEqual([item.locator for item in evidence], ["第 1 页", "第 2 页"])
        self.assertEqual(evidence[1].text, "第二节")

    def test_chunks_do_not_drop_text(self):
        evidence = [Evidence("x", str(i), "甲" * 10, "text") for i in range(5)]
        chunks = make_chunks(evidence, maximum_chars=21)
        self.assertEqual(sum(len(item.text) for chunk in chunks for item in chunk), 50)
        self.assertEqual(len(chunks), 3)

    def test_slugify_windows_characters(self):
        self.assertEqual(slugify('第一讲:GDP/价格?'), "第一讲_GDP_价格_")

    def test_completed_downloader_job_is_discovered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / "pku_recorder_app"
            output = app / "downloads"
            video = output / "测试课程" / "2026-10-01第1-2节.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"video")
            (app / "settings.json").write_text(json.dumps({"download_dir": ""}), encoding="utf-8")
            (app / "jobs.json").write_text(
                json.dumps({
                    "jobs": {
                        "a": {
                            "status": "done",
                            "course_name": "测试课程",
                            "file": "测试课程/2026-10-01第1-2节.mp4",
                        }
                    }
                }, ensure_ascii=False),
                encoding="utf-8",
            )
            found = discover_downloader_materials(root, "测试课程")
        self.assertEqual(found, [video.resolve()])


if __name__ == "__main__":
    unittest.main()


"""报告写入安全（原子写入、父目录创建、输入输出同文件保护）的回归测试。"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import triage_agent as ta  # noqa: E402


class WriteReportTextTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_new_file(self):
        target = self.root / "report.md"
        ta.write_report_text(target, "hello\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")

    def test_replaces_existing_file(self):
        target = self.root / "report.md"
        target.write_text("OLD", encoding="utf-8")
        ta.write_report_text(target, "NEW")
        self.assertEqual(target.read_text(encoding="utf-8"), "NEW")

    def test_parent_directory_is_created(self):
        target = self.root / "a" / "b" / "report.md"
        ta.write_report_text(target, "nested")
        self.assertTrue(target.is_file())

    def test_no_temporary_file_is_left_behind(self):
        target = self.root / "report.md"
        ta.write_report_text(target, "x")
        leftovers = list(self.root.glob(".veyra-report-*.tmp"))
        self.assertEqual(leftovers, [])

    def test_failed_replace_keeps_old_content_and_cleans_temp(self):
        target = self.root / "report.md"
        target.write_text("OLD", encoding="utf-8")
        with mock.patch.object(ta.os, "replace", side_effect=OSError(13, "denied")):
            with self.assertRaises(ta.ReportWriteError):
                ta.write_report_text(target, "NEW")
        self.assertEqual(target.read_text(encoding="utf-8"), "OLD")
        self.assertEqual(list(self.root.glob(".veyra-report-*.tmp")), [])

    def test_unwritable_parent_reports_chinese_error(self):
        # 用一个已存在的普通文件冒充父目录，mkdir 必然失败
        blocker = self.root / "blocker"
        blocker.write_text("x", encoding="utf-8")
        with self.assertRaises(ta.ReportWriteError) as ctx:
            ta.write_report_text(blocker / "report.md", "x")
        self.assertIn("无法创建输出目录", str(ctx.exception))


class SameFileDetectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_relative_and_absolute_paths(self):
        target = self.root / "log.txt"
        target.write_text("x", encoding="utf-8")
        previous = Path.cwd()
        # 测试目录可能位于与仓库不同的盘符（Windows），因此先切到测试目录可用单段相对路径。
        os.chdir(self.root)
        try:
            relative = os.path.relpath(target, Path.cwd())
            self.assertTrue(ta._paths_refer_to_same_file(str(target), relative))
        finally:
            os.chdir(previous)

    def test_dotdot_is_normalized(self):
        nested = self.root / "sub"
        nested.mkdir()
        target = self.root / "log.txt"
        target.write_text("x", encoding="utf-8")
        dotted = nested / ".." / "log.txt"
        self.assertTrue(ta._paths_refer_to_same_file(str(target), str(dotted)))

    def test_different_files_are_not_confused(self):
        first = self.root / "a.txt"
        second = self.root / "b.txt"
        first.write_text("a", encoding="utf-8")
        second.write_text("b", encoding="utf-8")
        self.assertFalse(ta._paths_refer_to_same_file(str(first), str(second)))

    def test_nonexistent_paths_compare_normalized_forms(self):
        target = self.root / "future.md"
        self.assertTrue(ta._paths_refer_to_same_file(str(target), str(self.root / "." / "future.md")))

    @unittest.skipUnless(os.name == "nt", "Windows 路径大小写不敏感")
    def test_windows_case_insensitive(self):
        target = self.root / "log.txt"
        target.write_text("x", encoding="utf-8")
        self.assertTrue(ta._paths_refer_to_same_file(str(target), str(target).upper()))


class RenderGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _quiet(self):
        return contextlib.redirect_stdout(io.StringIO())

    def test_refuses_when_output_equals_input(self):
        log = self.root / "crash.log"
        log.write_text("java.lang.NullPointerException: x\n", encoding="utf-8")
        before = log.read_text(encoding="utf-8")

        with self._quiet():
            ok = ta.render_report({}, [], output_path=str(log), source_path=str(log))

        self.assertFalse(ok)
        self.assertEqual(log.read_text(encoding="utf-8"), before)

    def test_allows_different_output_file(self):
        log = self.root / "crash.log"
        log.write_text("java.lang.NullPointerException: x\n", encoding="utf-8")
        out = self.root / "report.md"

        with self._quiet():
            ok = ta.render_report(
                {
                    "issue_title": "t",
                    "symptoms": "s",
                    "environment": {field: None for field in ta.ENVIRONMENT_FIELDS},
                    "reproduction_steps": [],
                    "missing_information": [],
                    "current_assessment": "a",
                    "evidence_refs": [],
                    "disclaimer": "d",
                },
                [],
                mode="local",
                output_path=str(out),
                source_path=str(log),
            )

        self.assertTrue(ok)
        self.assertTrue(out.is_file())
        self.assertEqual(log.read_text(encoding="utf-8"), "java.lang.NullPointerException: x\n")


if __name__ == "__main__":
    unittest.main()

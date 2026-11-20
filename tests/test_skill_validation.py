# -*- coding: utf-8 -*-
"""mod-issue-triage Skill 的 JSON 前置校验与 evals 用例回归测试。

不调用任何模型，也不执行 Skill 本身；这里验证的是“交给 Skill 之前的数据是否可靠”，
以及 evals 样本自身是否符合 schema_version 1.0.0 契约。
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "mod-issue-triage"
VALIDATOR = SKILL_DIR / "scripts" / "validate_issue_json.py"
CASES_DIR = SKILL_DIR / "evals" / "cases"
SCHEMA_FILE = ROOT / "docs" / "report.schema.json"


def load_validator():
    spec = importlib.util.spec_from_file_location("veyra_skill_validator", VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minimal_report():
    return {
        "schema_version": "1.0.0",
        "tool": {"name": "Veyra", "version": "1.2.0"},
        "mode": "local",
        "summary": {
            "issue_title": "Crash on start",
            "symptoms": "Game crashes on start.",
            "reproduction_steps": [],
            "current_assessment": "Not confirmed.",
            "evidence_refs": ["E1"],
            "disclaimer": "No root cause confirmed.",
        },
        "environment": {
            "game_version": "1.21.1",
            "java_version": None,
            "os": None,
            "loader": "NeoForge",
            "loader_version": "21.1.0",
        },
        "model_environment": None,
        "mods": [{"name": "demo_mod", "version": "1.0.0"}],
        "evidence": [
            {
                "id": "E1",
                "type": "exception",
                "start_line": 3,
                "end_line": 5,
                "content": "java.lang.RuntimeException: boom\n\tat a.B.c(B.java:1)\n\t... 2 more",
                "truncated": False,
            }
        ],
        "missing_information": ["Java version"],
        "conflicts": {},
        "warnings": [],
    }


class ValidateReportTest(unittest.TestCase):
    def setUp(self):
        self.validator = load_validator()

    def test_valid_report_has_no_errors(self):
        errors, warnings = self.validator.validate_report(minimal_report())
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_missing_required_field(self):
        data = minimal_report()
        del data["evidence"]
        errors, _ = self.validator.validate_report(data)
        self.assertTrue(any("evidence" in error for error in errors))

    def test_unsupported_major_version(self):
        data = minimal_report()
        data["schema_version"] = "2.0.0"
        errors, _ = self.validator.validate_report(data)
        self.assertTrue(any("主版本" in error for error in errors))

    def test_malformed_version(self):
        data = minimal_report()
        data["schema_version"] = "1.0"
        errors, _ = self.validator.validate_report(data)
        self.assertTrue(any("格式无效" in error for error in errors))

    def test_evidence_ref_must_exist(self):
        data = minimal_report()
        data["summary"]["evidence_refs"] = ["E9"]
        errors, _ = self.validator.validate_report(data)
        self.assertTrue(any("E9" in error for error in errors))

    def test_duplicate_evidence_id(self):
        data = minimal_report()
        data["evidence"].append(dict(data["evidence"][0]))
        errors, _ = self.validator.validate_report(data)
        self.assertTrue(any("重复" in error for error in errors))

    def test_invalid_line_range(self):
        data = minimal_report()
        data["evidence"][0]["start_line"] = 9
        data["evidence"][0]["end_line"] = 4
        errors, _ = self.validator.validate_report(data)
        self.assertTrue(any("行号区间" in error for error in errors))

    def test_environment_rejects_missing_key(self):
        data = minimal_report()
        del data["environment"]["os"]
        errors, _ = self.validator.validate_report(data)
        self.assertTrue(any("environment.os" in error for error in errors))

    def test_conflict_requires_non_empty_list(self):
        data = minimal_report()
        data["conflicts"] = {"game_version": []}
        errors, _ = self.validator.validate_report(data)
        self.assertTrue(any("conflicts.game_version" in error for error in errors))

    def test_model_environment_must_be_null_or_object(self):
        data = minimal_report()
        data["model_environment"] = "1.21.1"
        errors, _ = self.validator.validate_report(data)
        self.assertTrue(any("model_environment" in error for error in errors))

    def test_unknown_top_level_field_is_warning_only(self):
        data = minimal_report()
        data["extra"] = 1
        errors, warnings = self.validator.validate_report(data)
        self.assertEqual(errors, [])
        self.assertTrue(any("extra" in warning for warning in warnings))


class EvalCasesTest(unittest.TestCase):
    def setUp(self):
        self.validator = load_validator()
        self.case_files = sorted(CASES_DIR.glob("*.json"))

    def test_ten_cases_exist(self):
        self.assertEqual(len(self.case_files), 10)

    def test_every_case_report_is_contract_valid(self):
        for path in self.case_files:
            with self.subTest(case=path.name):
                case = json.loads(path.read_text(encoding="utf-8"))
                errors, warnings = self.validator.validate_report(case["report"])
                self.assertEqual(errors, [], f"{path.name}: {errors}")
                self.assertEqual(warnings, [], f"{path.name}: {warnings}")

    def test_every_case_cites_existing_evidence(self):
        for path in self.case_files:
            with self.subTest(case=path.name):
                case = json.loads(path.read_text(encoding="utf-8"))
                report = case["report"]
                ids = {item["id"] for item in report["evidence"]}
                for ref in report["summary"]["evidence_refs"]:
                    self.assertIn(ref, ids)

    def test_cases_have_expectations_and_forbidden(self):
        for path in self.case_files:
            with self.subTest(case=path.name):
                case = json.loads(path.read_text(encoding="utf-8"))
                self.assertTrue(case["expectations"])
                self.assertTrue(case["forbidden"])
                self.assertIn("language", case["player_issue"])
                self.assertIn("text", case["player_issue"])

    def test_case_ids_are_unique(self):
        ids = []
        for path in self.case_files:
            case = json.loads(path.read_text(encoding="utf-8"))
            ids.append(case["case_id"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_required_scenarios_are_covered(self):
        expectations = {
            "01-clear-crash": "明确崩溃日志",
            "02-mod-compatibility": "Mod 兼容性",
            "03-missing-environment": "环境信息缺失",
            "04-insufficient-evidence": "证据不足",
            "05-conflicting-versions": "冲突版本",
            "06-player-vs-log": "玩家描述与日志不一致",
            "07-prompt-injection": "提示注入",
            "08-non-english": "非英文",
            "09-no-player-description": "没有玩家描述",
            "10-resolution-evidence": "解决证据",
        }
        ids = {
            json.loads(path.read_text(encoding="utf-8"))["case_id"]
            for path in self.case_files
        }
        self.assertEqual(ids, set(expectations))


class ValidatorCliTest(unittest.TestCase):
    def run_validator(self, args, stdin_text=None):
        return subprocess.run(
            [sys.executable, str(VALIDATOR), *args],
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_valid_file_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue.json"
            path.write_text(json.dumps(minimal_report()), encoding="utf-8")
            result = self.run_validator([str(path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("契约校验通过", result.stdout)

    def test_invalid_file_exit_two(self):
        data = minimal_report()
        del data["summary"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = self.run_validator([str(path)])
        self.assertEqual(result.returncode, 2)
        self.assertIn("契约校验失败", result.stderr)

    def test_stdin_mode(self):
        result = self.run_validator(["-"], stdin_text=json.dumps(minimal_report()))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_broken_json_exit_one(self):
        result = self.run_validator(["-"], stdin_text="{ not json")
        self.assertEqual(result.returncode, 1)
        self.assertIn("JSON 解析失败", result.stderr)

    def test_case_mode(self):
        result = self.run_validator(["--case", str(CASES_DIR / "case-01-clear-crash.json")])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_quiet_suppresses_warnings(self):
        data = minimal_report()
        data["extra"] = 1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "issue.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = self.run_validator(["--quiet", str(path)])
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("[警告]", result.stderr)


class SchemaAgreementTest(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        self.validator = load_validator()

    def test_top_level_required_fields_match_validator(self):
        self.assertEqual(
            sorted(self.schema["required"]),
            sorted(self.validator.REQUIRED_TOP),
        )

    def test_evidence_required_fields_match_validator(self):
        self.assertEqual(
            sorted(self.schema["properties"]["evidence"]["items"]["required"]),
            sorted(self.validator.EVIDENCE_FIELDS),
        )

    def test_environment_fields_match_validator(self):
        self.assertEqual(
            sorted(self.schema["definitions"]["environment"]["required"]),
            sorted(self.validator.ENVIRONMENT_FIELDS),
        )

    def test_summary_fields_match_validator(self):
        self.assertEqual(
            sorted(self.schema["properties"]["summary"]["required"]),
            sorted(self.validator.SUMMARY_FIELDS),
        )


class SkillDocumentsTest(unittest.TestCase):
    SECTIONS = [
        "## 1. Issue Overview",
        "## 2. Environment",
        "## 3. Evidence Analysis",
        "## 4. Possible Causes",
        "## 5. Missing Information",
        "## 6. Recommended Actions",
        "## 7. Maintainer Reply Draft",
    ]

    def test_skill_lists_all_output_sections(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for section in self.SECTIONS:
            self.assertIn(section, text)

    def test_template_lists_all_output_sections(self):
        text = (SKILL_DIR / "references" / "report-template.md").read_text(encoding="utf-8")
        for section in self.SECTIONS:
            self.assertIn(section, text)

    def test_skill_keeps_source_labels(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for label in ("日志事实", "玩家陈述", "推测"):
            self.assertIn(label, text)

    def test_skill_requires_schema_version_check(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("schema_version", text)
        self.assertIn("1.0.0", text)

    def test_skill_states_no_github_automation(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("不自动", text)


if __name__ == "__main__":
    unittest.main()

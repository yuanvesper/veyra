"""JSON 数据契约（schema_version 1.0.0）与 CLI JSON 输出的回归测试。

所有用例使用临时目录，绝不写入仓库根目录的 report.md，也不读取真实玩家日志。
API 模式通过 patch call_model 模拟，不产生任何网络请求。
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "triage_agent.py"

sys.path.insert(0, str(REPO_ROOT))

import triage_agent as ta  # noqa: E402

TOP_LEVEL_FIELDS = [
    "schema_version",
    "tool",
    "mode",
    "summary",
    "environment",
    "model_environment",
    "mods",
    "evidence",
    "missing_information",
    "conflicts",
    "warnings",
]

ENVIRONMENT_FIELDS = [
    "game_version",
    "java_version",
    "os",
    "loader",
    "loader_version",
]

SUMMARY_FIELDS = [
    "issue_title",
    "symptoms",
    "reproduction_steps",
    "current_assessment",
    "evidence_refs",
    "disclaimer",
]

EVIDENCE_FIELDS = ["id", "type", "start_line", "end_line", "content", "truncated"]

CRASH_LOG = "\n".join(
    [
        "[10:00:00] [main/INFO]: Loading Minecraft 1.21.1 with NeoForge 21.1.0",
        "Found mod file demo_mod-1.0.0.jar",
        "java.lang.NullPointerException: demo failure",
        "\tat com.example.demo.Main.run(Main.java:10)",
        "\tat com.example.demo.Launcher.main(Launcher.java:5)",
        "Caused by: java.lang.IllegalStateException: bad state",
        "\tat com.example.demo.Main.run(Main.java:12)",
    ]
) + "\n"


def make_model_output(**overrides):
    """构造一份能通过校验的模型输出，用来模拟 API 模式。"""
    data = {
        "issue_title": "模型生成的标题",
        "symptoms": "模型整理的现象",
        "environment": {
            "game_version": "9.9.9",
            "loader": None,
            "loader_version": None,
            "mod_version": None,
            "java_version": None,
            "os": None,
        },
        "reproduction_steps": [],
        "missing_information": [],
        "current_assessment": "信息不足，无法判断。",
        "evidence_refs": ["E1"],
        "disclaimer": "模型生成的免责声明。",
    }
    data.update(overrides)
    return data


class JsonTestBase(unittest.TestCase):
    """临时目录、环境变量隔离与进程内 main() 调用工具。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / "crash.log"
        self.log.write_text(CRASH_LOG, encoding="utf-8")
        # main() 会改写这个全局开关；每个测试都从 Markdown 的默认值开始。
        ta._OUTPUT_TO_STDERR = False

    def tearDown(self):
        ta._OUTPUT_TO_STDERR = False
        self._tmp.cleanup()

    def isolated_env(self, **overrides):
        env = {key: value for key, value in os.environ.items()}
        for key in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "TRIAGE_LOCAL_MODE"):
            env.pop(key, None)
        env.update(overrides)
        return mock.patch.dict(os.environ, env, clear=True)

    def run_main(self, *args, env_overrides=None):
        """在进程内调用 main()，返回 (退出码, stdout, stderr)。"""
        overrides = (
            {"TRIAGE_LOCAL_MODE": "1"} if env_overrides is None else env_overrides
        )
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with self.isolated_env(**overrides):
            with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
                try:
                    ta.main([str(arg) for arg in args])
                except SystemExit as exc:
                    code = exc.code if exc.code is not None else 0
        return code, out.getvalue(), err.getvalue()

    def run_json(self, *args, env_overrides=None):
        code, out, err = self.run_main("--format", "json", *args, env_overrides=env_overrides)
        data = None
        if out.strip():
            data = json.loads(out)
        return code, data, out, err

    def run_cli(self, *args, env_overrides=None):
        env = {key: value for key, value in os.environ.items()}
        for key in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "TRIAGE_LOCAL_MODE"):
            env.pop(key, None)
        env["TRIAGE_LOCAL_MODE"] = "1"
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *[str(arg) for arg in args]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.root),
            env=env,
        )


class JsonStdoutTest(JsonTestBase):
    def test_stdout_is_parseable_json(self):
        code, data, out, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertIsInstance(data, dict)
        self.assertTrue(out.strip().startswith("{"))
        self.assertTrue(out.strip().endswith("}"))

    def test_stdout_contains_only_json(self):
        code, data, out, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertNotIn("[提取]", out)
        self.assertNotIn("[环境]", out)
        self.assertNotIn("[模式]", out)
        self.assertIn("[提取]", err)
        self.assertIn("[模式]", err)

    def test_required_top_level_fields(self):
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertEqual(list(data), TOP_LEVEL_FIELDS)

    def test_field_types(self):
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)

        self.assertIsInstance(data["schema_version"], str)
        self.assertEqual(set(data["tool"]), {"name", "version"})
        self.assertIsInstance(data["tool"]["name"], str)
        self.assertIsInstance(data["tool"]["version"], str)
        self.assertIn(data["mode"], ("local", "api"))

        self.assertEqual(set(data["summary"]), set(SUMMARY_FIELDS))
        self.assertIsInstance(data["summary"]["symptoms"], str)
        self.assertIsInstance(data["summary"]["reproduction_steps"], list)
        self.assertIsInstance(data["summary"]["evidence_refs"], list)

        self.assertEqual(set(data["environment"]), set(ENVIRONMENT_FIELDS))
        for value in data["environment"].values():
            self.assertTrue(value is None or isinstance(value, str))
        self.assertIsNone(data["model_environment"])

        self.assertIsInstance(data["mods"], list)
        for mod in data["mods"]:
            self.assertEqual(set(mod), {"name", "version"})

        self.assertIsInstance(data["evidence"], list)
        for entry in data["evidence"]:
            self.assertEqual(set(entry), set(EVIDENCE_FIELDS))
            self.assertIsInstance(entry["id"], str)
            self.assertIsInstance(entry["type"], str)
            self.assertIsInstance(entry["start_line"], int)
            self.assertIsInstance(entry["end_line"], int)
            self.assertIsInstance(entry["content"], str)
            self.assertIsInstance(entry["truncated"], bool)

        self.assertIsInstance(data["missing_information"], list)
        self.assertIsInstance(data["conflicts"], dict)
        self.assertIsInstance(data["warnings"], list)

    def test_schema_version_is_stable(self):
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertEqual(data["schema_version"], ta.SCHEMA_VERSION)

    def test_tool_version_matches_cli_version(self):
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertEqual(data["tool"], {"name": ta.TOOL_NAME, "version": ta.VERSION})

        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"{ta.TOOL_NAME} {ta.VERSION}")

    def test_unknown_environment_fields_are_null(self):
        code, data, out, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertIsNone(data["environment"]["java_version"])
        self.assertIsNone(data["environment"]["os"])
        # 序列化后是 JSON null，而不是 "None" 字符串或占位文本
        self.assertIn('"java_version": null', out)
        self.assertIn('"os": null', out)
        environment_block = out[out.index('"environment"'):out.index('"model_environment"')]
        self.assertNotIn("未提供", environment_block)


class JsonEnvironmentTest(JsonTestBase):
    def test_local_environment_values(self):
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertEqual(data["environment"]["game_version"], "1.21.1")
        self.assertEqual(data["environment"]["loader"], "NeoForge")
        self.assertEqual(data["environment"]["loader_version"], "21.1.0")

    def test_mods_stay_in_their_own_field(self):
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertEqual(data["mods"], [{"name": "demo_mod", "version": "1.0.0"}])
        # mod_version 是拼接摘要，不进入 JSON environment
        self.assertNotIn("mod_version", data["environment"])

    def test_conflicts_are_preserved_without_guessing(self):
        conflict_log = self.root / "conflict.log"
        conflict_log.write_text(
            "Minecraft Version: 1.21.1\n"
            "Minecraft Version: 1.20.4\n"
            "java.lang.NullPointerException: x\n",
            encoding="utf-8",
        )
        code, data, _, err = self.run_json(str(conflict_log))
        self.assertEqual(code, 0, err)
        self.assertIsNone(data["environment"]["game_version"])
        self.assertEqual(sorted(data["conflicts"]["game_version"]), ["1.20.4", "1.21.1"])
        missing = "\n".join(data["missing_information"])
        self.assertIn("多个不同候选值", missing)
        self.assertIn("1.20.4", missing)

    def test_missing_information_does_not_repeat_known_fields(self):
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        missing = "\n".join(data["missing_information"])
        self.assertNotIn("Minecraft 游戏版本", missing)
        self.assertNotIn("Mod 加载器", missing)
        self.assertNotIn("加载器版本", missing)
        self.assertIn("Java 版本", missing)
        self.assertIn("操作系统", missing)

    def test_api_mode_uses_python_environment(self):
        model_output = make_model_output(
            environment={
                "game_version": "9.9.9",
                "loader": "Forge",
                "loader_version": "0.0.1",
                "mod_version": "fake 1.0",
                "java_version": "99",
                "os": "FakeOS",
            }
        )
        with mock.patch.object(ta, "call_model", return_value=model_output):
            code, out, err = self.run_main(
                str(self.log),
                "--format",
                "json",
                env_overrides={"LLM_API_KEY": "test-key"},
            )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["mode"], "api")
        # Python 的事实优先，模型的值只出现在 model_environment
        self.assertEqual(data["environment"]["game_version"], "1.21.1")
        self.assertEqual(data["environment"]["loader"], "NeoForge")
        self.assertEqual(data["model_environment"]["game_version"], "9.9.9")
        self.assertEqual(data["model_environment"]["loader"], "Forge")

    def test_local_and_api_environments_match(self):
        local_out = self.root / "local.json"
        api_out = self.root / "api.json"

        code, out, err = self.run_main(
            str(self.log), "--format", "json", "-o", str(local_out)
        )
        self.assertEqual(code, 0, err)

        with mock.patch.object(ta, "call_model", return_value=make_model_output()):
            code, out, err = self.run_main(
                str(self.log),
                "--format",
                "json",
                "-o",
                str(api_out),
                env_overrides={"LLM_API_KEY": "test-key"},
            )
        self.assertEqual(code, 0, err)

        local = json.loads(local_out.read_text(encoding="utf-8"))
        api = json.loads(api_out.read_text(encoding="utf-8"))
        self.assertEqual(local["environment"], api["environment"])
        self.assertEqual(local["mods"], api["mods"])
        self.assertEqual(local["conflicts"], api["conflicts"])
        self.assertIsNone(local["model_environment"])
        self.assertIsNotNone(api["model_environment"])


class JsonEvidenceTest(JsonTestBase):
    def test_evidence_ids_match_extraction(self):
        text = self.log.read_text(encoding="utf-8")
        blocks, _ = ta.extract_evidence(text)
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertEqual([entry["id"] for entry in data["evidence"]], [block["id"] for block in blocks])

    def test_line_numbers_and_content_are_exact(self):
        text = self.log.read_text(encoding="utf-8")
        lines = text.splitlines()
        blocks, _ = ta.extract_evidence(text)
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)

        for entry, block in zip(data["evidence"], blocks):
            self.assertEqual(entry["start_line"], block["start_line"])
            self.assertEqual(entry["end_line"], block["end_line"])
            self.assertEqual(entry["content"], block["content"])
            self.assertEqual(entry["content"], "\n".join(lines[block["start_line"] - 1:block["end_line"]]))

    def test_evidence_is_not_renumbered(self):
        code, data, _, err = self.run_json(str(self.log))
        self.assertEqual(code, 0, err)
        self.assertEqual([entry["id"] for entry in data["evidence"]], ["E1", "E2"])
        self.assertEqual(data["summary"]["evidence_refs"], ["E1", "E2"])

    def test_truncation_state_is_preserved(self):
        long_log = self.root / "long.log"
        long_lines = ["java.lang.RuntimeException: too deep"]
        long_lines.extend(
            f"\tat com.example.Deep.frame{index}(Deep.java:{index})" for index in range(120)
        )
        long_log.write_text("\n".join(long_lines) + "\n", encoding="utf-8")

        code, data, _, err = self.run_json(str(long_log))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(data["evidence"]), 1)
        self.assertTrue(data["evidence"][0]["truncated"])
        self.assertTrue(data["evidence"][0]["content"].endswith("[已截断]"))


class JsonFileTest(JsonTestBase):
    def test_json_file_output_is_written_and_parseable(self):
        target = self.root / "reports" / "result.json"
        code, out, err = self.run_main(
            str(self.log), "--format", "json", "-o", str(target)
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "")
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(list(data), TOP_LEVEL_FIELDS)

    def test_markdown_default_is_unchanged(self):
        target = self.root / "report.md"
        code, out, err = self.run_main(str(self.log), "-o", str(target))
        self.assertEqual(code, 0, err)
        text = target.read_text(encoding="utf-8")
        self.assertIn("## 运行环境", text)
        self.assertIn("## 证据引用", text)
        self.assertNotIn("schema_version", text)
        self.assertIn("## 证据引用", out)

    def test_legacy_output_forms_still_work(self):
        equal_form = self.root / "equal.md"
        result = self.run_cli(str(self.log), f"--output={equal_form}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(equal_form.is_file())

        short_form = self.root / "short.md"
        result = self.run_cli(str(self.log), "-o", str(short_form))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(short_form.is_file())

    def test_invalid_format_exits_2(self):
        result = self.run_cli(str(self.log), "--format", "xml")
        self.assertEqual(result.returncode, 2)
        self.assertIn("不支持的输出格式", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_same_input_and_output_is_refused_in_json_mode(self):
        before = self.log.read_text(encoding="utf-8")
        result = self.run_cli(str(self.log), "--format", "json", "-o", str(self.log))
        self.assertEqual(result.returncode, 2)
        self.assertIn("拒绝写入", result.stderr)
        self.assertEqual(self.log.read_text(encoding="utf-8"), before)

    def test_failed_replace_keeps_old_file(self):
        target = self.root / "result.json"
        target.write_text("OLD", encoding="utf-8")
        with mock.patch.object(ta.os, "replace", side_effect=OSError(13, "denied")):
            with mock.patch.object(sys, "stdout", io.StringIO()), mock.patch.object(
                sys, "stderr", io.StringIO()
            ):
                ok = ta.write_contract_json({"schema_version": "1.0.0"}, target)
        self.assertFalse(ok)
        self.assertEqual(target.read_text(encoding="utf-8"), "OLD")
        self.assertEqual(list(self.root.glob(".veyra-report-*.tmp")), [])

    def test_write_failure_reports_no_success(self):
        out = io.StringIO()
        with mock.patch.object(
            ta, "write_report_text", side_effect=ta.ReportWriteError("无法写入报告")
        ), mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", io.StringIO()):
            ok = ta.write_contract_json({"schema_version": "1.0.0"}, self.root / "x.json")
        text = out.getvalue()
        self.assertFalse(ok)
        self.assertIn("错误", text)
        self.assertNotIn("已写入", text)

    def test_json_stdout_writes_no_report_file(self):
        report = REPO_ROOT / "report.md"
        before = report.read_bytes() if report.exists() else None

        result = self.run_cli(str(self.log), "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        json.loads(result.stdout)
        self.assertFalse((self.root / "report.md").exists())

        after = report.read_bytes() if report.exists() else None
        self.assertEqual(before, after)


class JsonSchemaFileTest(unittest.TestCase):
    def test_schema_file_matches_contract(self):
        schema_path = REPO_ROOT / "docs" / "report.schema.json"
        self.assertTrue(schema_path.is_file(), "docs/report.schema.json 缺失")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(schema["required"]), sorted(TOP_LEVEL_FIELDS))
        self.assertEqual(sorted(schema["properties"]), sorted(TOP_LEVEL_FIELDS))

        environment = schema["definitions"]["environment"]
        self.assertEqual(sorted(environment["required"]), sorted(ENVIRONMENT_FIELDS))

        evidence = schema["properties"]["evidence"]["items"]
        self.assertEqual(sorted(evidence["required"]), sorted(EVIDENCE_FIELDS))


if __name__ == "__main__":
    unittest.main()

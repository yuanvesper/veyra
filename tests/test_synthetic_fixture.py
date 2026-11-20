"""合成崩溃日志 fixture 的守护测试。

fixture 本身完全虚构（见文件第一行注释），这几个用例保证它不会悄悄失效：
证据 ID/行号、环境字段、Mod 列表，以及 CLI JSON 端到端路径。
测试不读取真实玩家日志。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import triage_agent as ta  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "synthetic-neoforge-crash.log"

EXPECTED_EVIDENCE = [
    ("E1", "exception", 7, 13),
    ("E2", "caused_by", 15, 18),
    ("E3", "suppressed", 20, 22),
]
EXPECTED_ENVIRONMENT = {
    "game_version": "1.21.1",
    "java_version": "21.0.7",
    "os": "Windows 11",
    "loader": "NeoForge",
    "loader_version": "21.1.0",
}
EXPECTED_MODS = [
    ("fixture_core", "1.4.2"),
    ("example_render", "2.0.0"),
    ("sample_lib", "0.9.1"),
]


class SyntheticFixtureTest(unittest.TestCase):
    def setUp(self):
        self.text = FIXTURE.read_text(encoding="utf-8")

    def test_fixture_is_synthetic_and_self_contained(self):
        self.assertIn("SYNTHETIC TEST FIXTURE", self.text)
        self.assertNotIn("real_crash", self.text)
        self.assertNotIn("Users\\", self.text)
        self.assertEqual(len(self.text.splitlines()), 34)

    def test_evidence_ids_and_line_numbers(self):
        evidence, injection = ta.extract_evidence(self.text)
        self.assertFalse(injection)
        self.assertEqual(
            [(item["id"], item["type"], item["start_line"], item["end_line"]) for item in evidence],
            EXPECTED_EVIDENCE,
        )
        self.assertTrue(evidence[0]["content"].startswith("java.lang.IllegalStateException:"))
        self.assertIn("Caused by: java.lang.NullPointerException", evidence[1]["content"])
        self.assertIn("Synthetic suppressed cleanup detail", evidence[2]["content"])
        self.assertTrue(all(item["truncated"] is False for item in evidence))

    def test_environment_and_mods(self):
        result = ta.extract_environment(self.text)
        environment = result["environment"]
        for field, value in EXPECTED_ENVIRONMENT.items():
            self.assertEqual(environment[field], value, field)
        self.assertEqual(
            environment["mod_version"],
            "fixture_core 1.4.2、example_render 2.0.0、sample_lib 0.9.1",
        )
        self.assertEqual(
            [(mod["name"], mod["version"]) for mod in result["mods"]],
            EXPECTED_MODS,
        )
        self.assertEqual(result["conflicts"], {})

    def test_cli_json_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "fixture.json"
            env = dict(os.environ)
            env["TRIAGE_LOCAL_MODE"] = "1"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "triage_agent.py"),
                    str(FIXTURE),
                    "--format",
                    "json",
                    "-o",
                    str(output),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=tmp,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            data = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertEqual(data["mode"], "local")
        self.assertEqual(data["environment"], EXPECTED_ENVIRONMENT)
        self.assertEqual(
            [(item["id"], item["type"], item["start_line"], item["end_line"]) for item in data["evidence"]],
            EXPECTED_EVIDENCE,
        )
        self.assertEqual(
            [(mod["name"], mod["version"]) for mod in data["mods"]],
            EXPECTED_MODS,
        )
        self.assertEqual(data["summary"]["evidence_refs"], ["E1", "E2", "E3"])
        self.assertEqual(data["conflicts"], {})
        self.assertEqual(data["warnings"], [])
        self.assertEqual(data["missing_information"], [])


if __name__ == "__main__":
    unittest.main()

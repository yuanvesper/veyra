"""环境信息抽取（extract_environment）的回归测试。

只验证“日志里明确写了什么就提取什么”，以及不猜测、不误认数字。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import triage_agent as ta  # noqa: E402


class EnvironmentExtractionTest(unittest.TestCase):
    def extract(self, text, description=""):
        return ta.extract_environment(text, description)["environment"]

    def test_minecraft_version(self):
        env = self.extract("Minecraft Version: 1.21.1")
        self.assertEqual(env["game_version"], "1.21.1")

    def test_java_version(self):
        env = self.extract("Java Version: 21.0.7, Microsoft")
        self.assertEqual(env["java_version"], "21.0.7")

    def test_operating_system_is_cleaned(self):
        env = self.extract("Operating System: Windows 11 (amd64) version 10.0")
        self.assertEqual(env["os"], "Windows 11")

    def test_loading_line_neoforge(self):
        env = self.extract("Loading Minecraft 1.21.1 with NeoForge 21.1.0")
        self.assertEqual(env["game_version"], "1.21.1")
        self.assertEqual(env["loader"], "NeoForge")
        self.assertEqual(env["loader_version"], "21.1.0")

    def test_loading_line_fabric(self):
        env = self.extract("Loading Minecraft 1.21.1 with Fabric Loader 0.16.9")
        self.assertEqual(env["loader"], "Fabric")
        self.assertEqual(env["loader_version"], "0.16.9")

    def test_loading_line_with_log_prefix(self):
        env = self.extract("[10:00:00] [main/INFO]: Loading Minecraft 1.21.1 with NeoForge 21.1.0")
        self.assertEqual(env["loader"], "NeoForge")

    def test_mod_file(self):
        result = ta.extract_environment("Found mod file examplemod-1.2.3.jar")
        self.assertEqual(result["mods"], [{"name": "examplemod", "version": "1.2.3"}])
        self.assertEqual(result["environment"]["mod_version"], "examplemod 1.2.3")

    def test_multiple_mod_files_are_all_kept(self):
        text = (
            "Found mod file demo_mod-1.0.0.jar\n"
            "Found mod file example_library-2.3.4.jar\n"
        )
        result = ta.extract_environment(text)
        self.assertEqual(len(result["mods"]), 2)
        self.assertEqual(
            result["environment"]["mod_version"],
            "demo_mod 1.0.0、example_library 2.3.4",
        )

    def test_mod_list_table_with_truncated_filename(self):
        text = (
            "Mod List: \n"
            "\t\tconfiguration-1.21.1-3.1.1-neoforge.jar |Configuration |configuration "
            "|3.1.1 |Manifest: NOSIGNATURE\n"
            "\t\tgtceu-1.21.1-8.0.0-SNAPSHOT+20260928-082847-70db06|GregTech |gtceu "
            "|8.0.0-SNAPSHOT+70db0|Manifest: NOSIGNATURE\n"
            "\t\tclient-1.21.1-20240808.144430-srg.jar |Minecraft |minecraft "
            "|1.21.1 |Manifest: a1:d4\n"
        )
        result = ta.extract_environment(text)
        names = [mod["name"] for mod in result["mods"]]
        self.assertIn("Configuration", names)
        self.assertIn("GregTech", names)
        # 平台自身（minecraft / neoforge）不算第三方 Mod
        self.assertNotIn("Minecraft", names)

    def test_missing_fields_stay_none(self):
        env = self.extract("Loading Minecraft 1.21.1 with NeoForge 21.1.0")
        self.assertIsNone(env["java_version"])
        self.assertIsNone(env["os"])
        self.assertIsNone(env["mod_version"])

    def test_conflicting_versions_are_not_guessed(self):
        text = "Minecraft Version: 1.21.1\nMinecraft Version: 1.20.4\n"
        result = ta.extract_environment(text)
        self.assertIsNone(result["environment"]["game_version"])
        self.assertEqual(sorted(result["conflicts"]["game_version"]), ["1.20.4", "1.21.1"])

    def test_duplicate_identical_versions_are_not_a_conflict(self):
        text = "Minecraft Version: 1.21.1\nMinecraft Version ID: 1.21.1\n"
        result = ta.extract_environment(text)
        self.assertEqual(result["environment"]["game_version"], "1.21.1")
        self.assertNotIn("game_version", result["conflicts"])

    def test_irrelevant_numbers_are_ignored(self):
        text = (
            "[10:00:00] [main/INFO]: Loaded 1234 recipes in 3.5s\n"
            "Found 12 files\n"
        )
        env = self.extract(text)
        for field in ("game_version", "java_version", "loader", "loader_version"):
            self.assertIsNone(env[field], msg=field)

    def test_description_needs_explicit_label(self):
        env = self.extract("", "我用的是 1.21.1，Java 21")
        self.assertIsNone(env["game_version"])
        env = self.extract("", "游戏版本：1.21.1")
        self.assertEqual(env["game_version"], "1.21.1")

    def test_missing_information_skips_recognized_fields(self):
        text = "Minecraft Version: 1.21.1\nJava Version: 21.0.7, Microsoft\n"
        environment_result = ta.extract_environment(text)
        output = ta.build_local_output("", [], False, environment_result)
        missing = "\n".join(output["missing_information"])
        self.assertNotIn("Minecraft 游戏版本", missing)
        self.assertNotIn("Java 版本", missing)
        self.assertIn("操作系统", missing)

    def test_conflict_is_reported_in_missing_information(self):
        text = "Minecraft Version: 1.21.1\nMinecraft Version: 1.20.4\n"
        environment_result = ta.extract_environment(text)
        output = ta.build_local_output("", [], False, environment_result)
        missing = "\n".join(output["missing_information"])
        self.assertIn("多个不同候选值", missing)
        self.assertIn("1.20.4", missing)


if __name__ == "__main__":
    unittest.main()

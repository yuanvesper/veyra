"""命令行参数（--help / --version / -o / 错误输入）的回归测试。

所有用例都在临时目录里运行，不会写入仓库根目录的 report.md。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "triage_agent.py"

CRASH_LOG = (
    "[10:00:00] [main/INFO]: Loading Minecraft 1.21.1 with NeoForge 21.1.0\n"
    "[10:00:01] [main/ERROR]: Crash detected\n"
    "java.lang.NullPointerException: demo failure\n"
    "\tat com.example.demo.Main.run(Main.java:10)\n"
)


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / "crash.log"
        self.log.write_text(CRASH_LOG, encoding="utf-8")

        env = {key: value for key, value in os.environ.items()}
        env.pop("LLM_API_KEY", None)
        env.pop("DEEPSEEK_API_KEY", None)
        env["TRIAGE_LOCAL_MODE"] = "1"
        self.env = env

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(self.root),
            env=self.env,
        )

    def test_help(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertIn("日志路径", result.stdout)
        self.assertIn("--version", result.stdout)

    def test_version(self):
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"Veyra {self.version()}")

    def version(self):
        sys.path.insert(0, str(REPO_ROOT))
        import triage_agent as ta

        return ta.VERSION

    def test_positional_and_output_option(self):
        out = self.root / "out.md"
        result = self.run_cli(str(self.log), "-o", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.is_file())
        self.assertIn("## 证据引用", out.read_text(encoding="utf-8"))

    def test_output_equals_form(self):
        out = self.root / "equal.md"
        result = self.run_cli(str(self.log), f"--output={out}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.is_file())

    def test_missing_output_value_exits_2(self):
        result = self.run_cli(str(self.log), "-o")
        self.assertEqual(result.returncode, 2)
        self.assertIn("缺少输出路径", result.stdout + result.stderr)

    def test_extra_argument_exits_2(self):
        result = self.run_cli(str(self.log), "extra.log")
        self.assertEqual(result.returncode, 2)
        self.assertIn("无法识别的多余参数", result.stdout + result.stderr)

    def test_same_input_and_output_is_refused(self):
        before = self.log.read_text(encoding="utf-8")
        result = self.run_cli(str(self.log), "-o", str(self.log))
        self.assertEqual(result.returncode, 2)
        self.assertIn("拒绝写入", result.stdout + result.stderr)
        self.assertEqual(self.log.read_text(encoding="utf-8"), before)

    def test_missing_log_exits_1(self):
        result = self.run_cli(str(self.root / "no-such.log"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("日志文件不存在", result.stdout + result.stderr)

    def test_missing_parent_directory_is_created(self):
        out = self.root / "reports" / "nested" / "report.md"
        result = self.run_cli(str(self.log), "-o", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(out.is_file())

    def test_default_output_is_relative_to_cwd(self):
        result = self.run_cli(str(self.log))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "report.md").is_file())


if __name__ == "__main__":
    unittest.main()

"""CLI tests: exit codes, diagnostics and machine readable output."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from pack_helpers import base_manifest, file_entry, run_cli, write_pack

from veyra_pack.contract import PACK_TOOL_VERSION


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)


class VersionAndUsageTests(CliTestCase):
    def test_version_exits_zero(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            run_cli(["--version"])
        self.assertEqual(caught.exception.code, 0)

    def test_module_entry_point_prints_version(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-m", "veyra_pack", "--version"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(f"veyra-pack {PACK_TOOL_VERSION}", completed.stdout)

    def test_missing_required_option_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            run_cli(["init", str(self.root / "kit"), "--type", "other"])
        self.assertEqual(caught.exception.code, 2)

    def test_unknown_pack_type_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            run_cli(
                ["init", str(self.root / "kit"), "--name", "Kit", "--type", "nope"]
            )
        self.assertEqual(caught.exception.code, 2)


class ValidateCommandTests(CliTestCase):
    def test_valid_pack_exits_zero(self) -> None:
        write_pack(self.root / "pack")
        code, out, err = run_cli(["validate", str(self.root / "pack")])
        self.assertEqual(code, 0, err)
        self.assertIn("pack valid", out)

    def test_invalid_pack_exits_one_and_prints_code(self) -> None:
        write_pack(
            self.root / "pack",
            manifest_text=json.dumps(base_manifest(files=[{"path": "../x"}])),
        )
        code, out, err = run_cli(["validate", str(self.root / "pack")])
        self.assertEqual(code, 1)
        self.assertIn("E_PATH_TRAVERSAL", err)

    def test_json_output_is_parseable(self) -> None:
        payload = b"x"
        data = base_manifest(files=[file_entry("files/a.bin", payload)])
        write_pack(self.root / "pack", data, {"files/a.bin": payload})
        code, out, err = run_cli(["validate", str(self.root / "pack"), "--json"])
        self.assertEqual(code, 0, err)
        parsed = json.loads(out)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["errors"], [])
        self.assertEqual(parsed["file_count"], 1)

    def test_json_output_reports_errors(self) -> None:
        write_pack(self.root / "pack", base_manifest(schema_version="9.0.0"))
        code, out, err = run_cli(["validate", str(self.root / "pack"), "--json"])
        self.assertEqual(code, 1)
        parsed = json.loads(out)
        self.assertFalse(parsed["ok"])
        codes = {issue["code"] for issue in parsed["errors"]}
        self.assertIn("E_SCHEMA_UNSUPPORTED", codes)
        self.assertTrue(all("location" in issue for issue in parsed["errors"]))

    def test_missing_manifest_exits_one(self) -> None:
        (self.root / "pack").mkdir()
        code, out, err = run_cli(["validate", str(self.root / "pack")])
        self.assertEqual(code, 1)
        self.assertIn("E_MANIFEST_MISSING", err)


class ExportCommandTests(CliTestCase):
    def test_export_then_protect_existing_output(self) -> None:
        write_pack(self.root / "pack")
        output = self.root / "out.zip"
        code, out, err = run_cli(["export", str(self.root / "pack"), "-o", str(output)])
        self.assertEqual(code, 0, err)
        self.assertTrue(zipfile.is_zipfile(output))

        code, out, err = run_cli(["export", str(self.root / "pack"), "-o", str(output)])
        self.assertEqual(code, 1)
        self.assertIn("E_OUTPUT_EXISTS", err)

        code, out, err = run_cli(
            ["export", str(self.root / "pack"), "-o", str(output), "--overwrite"]
        )
        self.assertEqual(code, 0, err)

    def test_init_validate_export_round_trip(self) -> None:
        code, out, err = run_cli(
            [
                "init",
                str(self.root / "kit"),
                "--name",
                "Round Trip",
                "--type",
                "asset_pack",
            ]
        )
        self.assertEqual(code, 0, err)
        code, out, err = run_cli(["validate", str(self.root / "kit")])
        self.assertEqual(code, 0, err)
        code, out, err = run_cli(
            ["export", str(self.root / "kit"), "-o", str(self.root / "kit.zip")]
        )
        self.assertEqual(code, 0, err)
        with zipfile.ZipFile(self.root / "kit.zip") as archive:
            self.assertEqual(archive.namelist(), ["pack.json"])


if __name__ == "__main__":
    unittest.main()

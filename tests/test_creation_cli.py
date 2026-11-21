"""Tests for the ``python -m veyra_creation`` command line surface."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from creation_helpers import run_creation_cli, write_file

from veyra_creation.store import ProjectStore


class CreationCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "project"

    def test_init_json_reports_the_new_project(self) -> None:
        code, out, err = run_creation_cli(
            [
                "init",
                str(self.root),
                "--name",
                "Suoming Theme",
                "--pack-type",
                "desktop_theme",
                "--target-game",
                "Wuthering Waves",
                "--json",
            ]
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["project_id"].startswith("prj_"))
        self.assertEqual(payload["spec_revision"], 1)
        self.assertTrue((self.root / "creation" / "state.json").is_file())

    def test_init_uses_the_directory_name_by_default(self) -> None:
        code, out, err = run_creation_cli(["init", str(self.root), "--json"])
        self.assertEqual(code, 0, err)
        spec = json.loads((self.root / "creation" / "spec.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["project"]["name"], self.root.name)

    def test_init_twice_fails_with_exit_code_two(self) -> None:
        self.assertEqual(run_creation_cli(["init", str(self.root)])[0], 0)
        code, _out, err = run_creation_cli(["init", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("E_PROJECT_EXISTS", err)

    def test_status_json(self) -> None:
        self.assertEqual(run_creation_cli(["init", str(self.root)])[0], 0)
        code, out, err = run_creation_cli(["status", str(self.root), "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["spec"]["revision"], 1)
        self.assertIsNone(payload["plan"])

    def test_status_on_a_missing_project_fails(self) -> None:
        code, _out, err = run_creation_cli(["status", str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn("E_STATE_MISSING", err)

    def test_digest_prints_json_and_optionally_writes_it(self) -> None:
        self.assertEqual(run_creation_cli(["init", str(self.root)])[0], 0)
        code, out, err = run_creation_cli(["digest", str(self.root)])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["digest_id"].startswith("dig_"))
        self.assertFalse((self.root / "creation" / "digest.json").exists())
        code, out, err = run_creation_cli(["digest", str(self.root), "--write"])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / "creation" / "digest.json").is_file())
        saved = json.loads((self.root / "creation" / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["digest_id"], payload["digest_id"])

    def test_verify_returns_zero_for_a_clean_project(self) -> None:
        self.assertEqual(run_creation_cli(["init", str(self.root)])[0], 0)
        code, out, err = run_creation_cli(["verify", str(self.root)])
        self.assertEqual(code, 0, err)
        self.assertIn("OK", out)

    def test_verify_returns_one_for_a_broken_lock(self) -> None:
        self.assertEqual(run_creation_cli(["init", str(self.root)])[0], 0)
        store = ProjectStore(self.root)
        write_file(self.root, "assets/avatar.png", b"v1")
        store.lock("assets/avatar.png")
        write_file(self.root, "assets/avatar.png", b"v2")
        code, out, err = run_creation_cli(["verify", str(self.root), "--json"])
        self.assertEqual(code, 1, err)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("E_LOCK_DRIFT", {item["code"] for item in payload["errors"]})

    def test_version_and_help_are_available(self) -> None:
        from veyra_creation.cli import main

        with self.assertRaises(SystemExit) as version:
            main(["--version"])
        self.assertEqual(version.exception.code, 0)
        with self.assertRaises(SystemExit) as help_exit:
            main(["--help"])
        self.assertEqual(help_exit.exception.code, 0)


if __name__ == "__main__":
    unittest.main()

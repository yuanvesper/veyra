"""R4.1 regression tests for empty project paths.

The empty string must be rejected before ``Path("")`` turns it into ``Path(".")``.
These tests deliberately exercise both the real constructor and the real CLI,
not just ``normalize_lexical_path``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path

from veyra_creation.errors import CreationError
from veyra_creation.store import ProjectStore


REPO_ROOT = Path(__file__).resolve().parents[1]


class EmptyProjectPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        self.env = os.environ.copy()
        existing = self.env.get("PYTHONPATH")
        self.env["PYTHONPATH"] = (
            str(REPO_ROOT)
            if not existing
            else str(REPO_ROOT) + os.pathsep + existing
        )

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "veyra_creation", *args],
            cwd=str(self.cwd),
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    def _assert_no_creation(self, directory: Path) -> None:
        self.assertFalse((directory / "creation").exists())
        leftovers = sorted(item.name for item in directory.iterdir())
        self.assertEqual(leftovers, [])

    def test_constructor_rejects_empty_string(self) -> None:
        with chdir(self.cwd):
            with self.assertRaises(CreationError) as caught:
                ProjectStore("")
        self.assertEqual(caught.exception.code, "E_PROJECT_PATH_INVALID")
        self._assert_no_creation(self.cwd)

    def test_cli_empty_init_returns_nonzero(self) -> None:
        completed = self._run_cli(
            "init", "", "--name", "empty", "--pack-type", "desktop_theme"
        )
        self.assertNotEqual(completed.returncode, 0)
        self._assert_no_creation(self.cwd)

    def test_cli_empty_error_contains_code(self) -> None:
        completed = self._run_cli(
            "init", "", "--name", "empty", "--pack-type", "desktop_theme"
        )
        self.assertIn("E_PROJECT_PATH_INVALID", completed.stderr)

    def test_cli_empty_creates_no_files(self) -> None:
        completed = self._run_cli(
            "init", "", "--name", "empty", "--pack-type", "desktop_theme"
        )
        self.assertNotEqual(completed.returncode, 0)
        self._assert_no_creation(self.cwd)

    def test_constructor_dot_initializes(self) -> None:
        with chdir(self.cwd):
            store = ProjectStore(".")
            store.init(name="dot", pack_type="desktop_theme")
        self.assertTrue((self.cwd / "creation" / "state.json").is_file())

    def test_constructor_path_dot_initializes(self) -> None:
        with chdir(self.cwd):
            store = ProjectStore(Path("."))
            store.init(name="path-dot", pack_type="desktop_theme")
        self.assertTrue((self.cwd / "creation" / "state.json").is_file())

    def test_cli_dot_initializes(self) -> None:
        completed = self._run_cli("init", ".", "--name", "dot", "--pack-type", "desktop_theme")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((self.cwd / "creation" / "state.json").is_file())

    def test_cli_absolute_path_initializes(self) -> None:
        target = self.root / "absolute-project"
        completed = self._run_cli(
            "init", str(target), "--name", "absolute", "--pack-type", "desktop_theme"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((target / "creation" / "state.json").is_file())

    def test_cli_relative_path_initializes(self) -> None:
        completed = self._run_cli(
            "init", "relative-project", "--name", "relative", "--pack-type", "desktop_theme"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(
            (self.cwd / "relative-project" / "creation" / "state.json").is_file()
        )


if __name__ == "__main__":
    unittest.main()

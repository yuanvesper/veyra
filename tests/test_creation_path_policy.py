"""R4 negative tests for the Windows project-root path policy.

The policy is intentionally conservative: only ordinary absolute drive paths
and ordinary relative paths are accepted.  These tests use real junctions so a
constructor that merely defers rejection to ``init`` cannot pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from veyra_creation.errors import CreationError
from veyra_creation.store import ProjectStore
from veyra_pack.paths import PathPolicyError, normalize_lexical_path


def _make_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return False
    try:
        status = os.lstat(link)
    except OSError:
        return False
    return bool(getattr(status, "st_file_attributes", 0) & 0x400)


@unittest.skipUnless(os.name == "nt", "Windows root-path policy tests")
class WindowsPathPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)

    def test_normal_absolute_drive_path_is_accepted(self) -> None:
        store = ProjectStore(self.base / "normal-project")
        state = store.init(name="normal", pack_type="desktop_theme")
        self.assertEqual(state["spec_revision"], 1)
        self.assertTrue(store.state_path.is_file())

    def test_normal_unc_and_extended_unc_are_rejected(self) -> None:
        for raw in (r"\\server\share\project", r"\\?\UNC\server\share\project"):
            with self.subTest(raw=raw):
                with self.assertRaises(CreationError) as caught:
                    ProjectStore(raw)
                self.assertIn(
                    caught.exception.code,
                    {"E_PROJECT_PATH_UNC", "E_PROJECT_PATH_DEVICE"},
                )

    def test_device_namespaces_are_rejected(self) -> None:
        for raw in (
            r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1\project",
            r"\\.\C:\project",
            r"\\?\C:\project",
            r"\??\C:\project",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(CreationError) as caught:
                    ProjectStore(raw)
                self.assertEqual(caught.exception.code, "E_PROJECT_PATH_DEVICE")

    def test_drive_relative_paths_are_rejected(self) -> None:
        for raw in ("C:foo", "C:"):
            with self.subTest(raw=raw):
                with self.assertRaises(CreationError) as caught:
                    ProjectStore(raw)
                self.assertEqual(caught.exception.code, "E_PROJECT_PATH_DRIVE_RELATIVE")

    def test_root_relative_path_without_a_drive_is_rejected(self) -> None:
        with self.assertRaises(CreationError) as caught:
            ProjectStore(r"\project")
        self.assertEqual(caught.exception.code, "E_PROJECT_PATH_INVALID")

    def test_trailing_dot_and_space_components_are_rejected(self) -> None:
        for component in ("...", ".. ", ". ", "... ", "link.", "link "):
            with self.subTest(component=component):
                with self.assertRaises(CreationError) as caught:
                    ProjectStore(self.base / component / "project")
                self.assertEqual(caught.exception.code, "E_PROJECT_PATH_TRAILING")

    def test_ads_reserved_names_and_invalid_characters_are_rejected(self) -> None:
        cases = {
            self.base / "project:stream": "E_PROJECT_PATH_ADS",
            self.base / "CON" / "project": "E_PROJECT_PATH_RESERVED",
            self.base / "bad?name" / "project": "E_PROJECT_PATH_INVALID",
        }
        for path, code in cases.items():
            with self.subTest(path=path):
                with self.assertRaises(CreationError) as caught:
                    ProjectStore(path)
                self.assertEqual(caught.exception.code, code)

    def test_empty_path_is_rejected(self) -> None:
        with self.assertRaises(PathPolicyError) as caught:
            normalize_lexical_path("")
        self.assertEqual(caught.exception.code, "E_PROJECT_PATH_INVALID")


@unittest.skipUnless(os.name == "nt", "Windows junction tests")
class PathPolicyJunctionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.outside = self.base / "outside"
        self.outside.mkdir(parents=True)

    def _junction(self, name: str = "link") -> Path:
        link = self.base / name
        if not _make_junction(link, self.outside):
            self.skipTest("junction creation is not available")
        return link

    def _assert_constructor_rejects(self, path: Path, code: str) -> None:
        with self.assertRaises(CreationError) as caught:
            ProjectStore(path)
        self.assertEqual(caught.exception.code, code)
        self.assertFalse((self.outside / "project" / "creation" / "state.json").exists())
        self.assertFalse((self.outside / "creation" / "state.json").exists())

    def test_three_dot_component_cannot_reach_a_junction(self) -> None:
        self._junction()
        self._assert_constructor_rejects(
            self.base / "..." / "link" / "project",
            "E_PROJECT_PATH_TRAILING",
        )

    def test_trailing_space_parent_cannot_reach_a_junction(self) -> None:
        self._junction()
        self._assert_constructor_rejects(
            self.base / ".. " / "link" / "project",
            "E_PROJECT_PATH_TRAILING",
        )

    def test_missing_parent_with_trailing_space_cannot_reach_a_junction(self) -> None:
        self._junction()
        self._assert_constructor_rejects(
            self.base / "missing" / ".. " / "link" / "project",
            "E_PROJECT_PATH_TRAILING",
        )

    def test_missing_parent_with_three_dots_cannot_reach_a_junction(self) -> None:
        self._junction()
        self._assert_constructor_rejects(
            self.base / "missing" / "..." / "link" / "project",
            "E_PROJECT_PATH_TRAILING",
        )

    def test_junction_spelled_with_trailing_dot_is_rejected(self) -> None:
        self._junction()
        self._assert_constructor_rejects(
            self.base / "link." / "project",
            "E_PROJECT_PATH_TRAILING",
        )

    def test_junction_spelled_with_trailing_space_is_rejected(self) -> None:
        self._junction()
        self._assert_constructor_rejects(
            self.base / "link " / "project",
            "E_PROJECT_PATH_TRAILING",
        )

    def test_cli_rejects_a_trailing_dot_bypass(self) -> None:
        self._junction()
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "veyra_creation",
                "init",
                str(self.base / "..." / "link" / "project"),
                "--name",
                "Bypass",
                "--pack-type",
                "desktop_theme",
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("E_PROJECT_PATH_TRAILING", completed.stderr)
        self.assertFalse((self.outside / "project" / "creation" / "state.json").exists())

    def test_r3_missing_parent_junction_is_still_rejected(self) -> None:
        self._junction()
        self._assert_constructor_rejects(
            self.base / "missing" / ".." / "link" / "project",
            "E_LINK_BOUNDARY",
        )

    def test_ancestor_junction_introduced_after_construction_is_still_rejected(self) -> None:
        late_parent = self.base / "late-ancestor"
        store = ProjectStore(late_parent / "project")
        if not _make_junction(late_parent, self.outside):
            self.skipTest("junction creation is not available")
        with self.assertRaises(CreationError) as caught:
            store.init(name="late ancestor", pack_type="desktop_theme")
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertFalse((self.outside / "project").exists())

    def test_all_entrypoints_reject_a_replaced_ancestor(self) -> None:
        container = self.base / "container"
        store = ProjectStore(container / "project")
        store.init(name="entrypoint project", pack_type="desktop_theme")
        backup = self.base / "container-backup"
        container.rename(backup)
        if not _make_junction(container, self.outside):
            self.skipTest("junction creation is not available")

        calls = {
            "init": lambda: store.init(name="again", pack_type="desktop_theme"),
            "load_state": store.load_state,
            "load_spec": store.load_spec,
            "load_decisions": store.load_decisions,
            "append_decision": lambda: store.append_decision("note"),
            "lock": lambda: store.lock("artifact.txt"),
            "build_digest": store.build_digest,
            "verify": store.verify,
        }
        for name, call in calls.items():
            with self.subTest(entrypoint=name):
                if name == "verify":
                    issues = call()
                    self.assertIn("E_LINK_BOUNDARY", {issue.code for issue in issues})
                else:
                    with self.assertRaises(CreationError) as caught:
                        call()
                    self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertFalse((self.outside / "project").exists())


_REPO_ROOT = Path(__file__).resolve().parents[1]
_AMBIGUOUS_COMPONENTS = {
    "two_dots_space": ".. ",
    "three_dots": "...",
    "four_dots": "....",
    "trailing_dot": "ambiguous.",
    "trailing_space": "ambiguous ",
}


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "veyra_creation", *args],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


@unittest.skipUnless(os.name == "nt", "Windows path boundary verification")
class IndependentPathBoundaryVerificationTests(unittest.TestCase):
    """Independent §5.6 checks; no production code is modified."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.outside = self.base / "outside"
        self.outside.mkdir(parents=True)

    def assertNoProjectState(self) -> None:
        self.assertFalse((self.base / "creation").exists())
        self.assertFalse((self.outside / "project").exists())
        self.assertFalse((self.outside / "creation").exists())

    def test_ambiguous_component_is_rejected_by_constructor(self) -> None:
        for label, component in _AMBIGUOUS_COMPONENTS.items():
            with self.subTest(case=label):
                target = self.base / component / "project"
                with self.assertRaises(CreationError) as caught:
                    ProjectStore(target)
                self.assertEqual(caught.exception.code, "E_PROJECT_PATH_TRAILING")
                self.assertNoProjectState()
                self.assertFalse(target.exists())
                self.assertFalse((self.base / component / "project").exists())

    def test_missing_parent_with_ambiguous_component_cannot_reach_a_junction(self) -> None:
        link = self.base / "link"
        if not _make_junction(link, self.outside):
            self.skipTest("junction creation is not available")
        for label, component in _AMBIGUOUS_COMPONENTS.items():
            with self.subTest(case=label):
                path = self.base / "missing" / component / "link" / "project"
                with self.assertRaises(CreationError) as caught:
                    ProjectStore(path)
                self.assertEqual(caught.exception.code, "E_PROJECT_PATH_TRAILING")
                self.assertNoProjectState()
                self.assertFalse((self.base / "missing").exists())

    def test_cli_rejects_ambiguous_components(self) -> None:
        for label, component in _AMBIGUOUS_COMPONENTS.items():
            with self.subTest(case=label):
                target = self.base / component / "project"
                completed = _run_cli(
                    "init",
                    str(target),
                    "--name",
                    "Bypass",
                    "--pack-type",
                    "desktop_theme",
                )
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertIn("E_PROJECT_PATH_TRAILING", completed.stderr)
                self.assertNoProjectState()
                self.assertFalse(target.exists())

    def test_symlink_component_is_rejected_when_available(self) -> None:
        link = self.base / "symlink-link"
        try:
            os.symlink(self.outside, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlink creation is not available: {exc}")
        with self.assertRaises(CreationError) as caught:
            ProjectStore(link / "project")
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertNoProjectState()


@unittest.skipUnless(os.name == "nt", "Windows positive path compatibility")
class PathPolicyPositiveCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)

    def test_dot_and_relative_constructor_forms(self) -> None:
        previous = os.getcwd()
        os.chdir(self.base)
        self.addCleanup(os.chdir, previous)
        for raw in (".", Path(".")):
            with self.subTest(raw=raw):
                self.assertEqual(ProjectStore(raw).root, self.base.resolve())
        self.assertEqual(
            ProjectStore("relative-project").root,
            (self.base / "relative-project").resolve(),
        )

    def test_normal_absolute_and_missing_parent_initialize(self) -> None:
        absolute = ProjectStore(self.base / "absolute-project")
        absolute.init(name="absolute", pack_type="desktop_theme")
        self.assertTrue(absolute.state_path.is_file())

        missing_parent = ProjectStore(self.base / "missing" / "normal-project")
        missing_parent.init(name="missing-parent", pack_type="desktop_theme")
        self.assertTrue(missing_parent.state_path.is_file())

    def test_lifecycle_methods_work_on_a_legal_path(self) -> None:
        store = ProjectStore(self.base / "lifecycle")
        store.init(name="lifecycle", pack_type="desktop_theme")

        status = store.status()
        self.assertEqual(status["spec"]["revision"], 1)
        self.assertIsNone(status["plan"])

        digest = store.build_digest(write=True)
        self.assertEqual(digest["project_id"], store.load_state()["project_id"])
        self.assertTrue(store.digest_path.is_file())

        errors = [issue for issue in store.verify() if issue.is_error]
        self.assertEqual(errors, [])

    def test_cli_init_status_digest_verify_on_a_legal_path(self) -> None:
        path = str(self.base / "cli-project")
        init = _run_cli(
            "init",
            path,
            "--name",
            "cli",
            "--pack-type",
            "desktop_theme",
        )
        self.assertEqual(init.returncode, 0, init.stderr)
        self.assertTrue((Path(path) / "creation" / "state.json").is_file())

        status = _run_cli("status", path, "--json")
        self.assertEqual(status.returncode, 0, status.stderr)

        digest = _run_cli("digest", path, "--write")
        self.assertEqual(digest.returncode, 0, digest.stderr)
        self.assertTrue((Path(path) / "creation" / "digest.json").is_file())

        verify = _run_cli("verify", path, "--json")
        self.assertEqual(verify.returncode, 0, verify.stderr)


if __name__ == "__main__":
    unittest.main()

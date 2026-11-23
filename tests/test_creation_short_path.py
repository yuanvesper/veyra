"""Windows 8.3 alias and reparse-point coverage for ProjectStore roots."""

from __future__ import annotations

import ctypes
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import veyra_creation.store as store_module
import veyra_pack.paths as paths_module
from veyra_creation.errors import CreationError
from veyra_creation.store import ProjectStore
from veyra_pack.paths import (
    _canonical_alias_path,
    _root_paths_equivalent,
    _windows_long_name,
    is_link_like,
)


def _short_path(path: Path) -> Path:
    if os.name != "nt":
        raise unittest.SkipTest("Windows short-name tests")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetShortPathNameW
    function.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    function.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    written = function(str(path), buffer, len(buffer))
    if written == 0:
        raise unittest.SkipTest("GetShortPathNameW is unavailable")
    short = Path(buffer.value)
    if short == path or "~" not in str(short):
        raise unittest.SkipTest("this volume does not expose an 8.3 alias")
    return short


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


class _FakeLongPathFunction:
    def __init__(
        self,
        return_value: int,
        *,
        value: str | None = None,
        error: int | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.return_value = return_value
        self.value = value
        self.error = error
        self.raises = raises
        self.calls: list[tuple[str, int]] = []

    def __call__(self, source: str, output: object, size: int) -> int:
        self.calls.append((source, size))
        if self.raises is not None:
            raise self.raises
        if self.error is not None:
            ctypes.set_last_error(self.error)
        if self.value is not None:
            output.value = self.value  # type: ignore[attr-defined]
        return self.return_value


@unittest.skipUnless(os.name == "nt", "Windows short-path tests")
class ShortPathRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)

    def test_short_root_is_accepted_and_initializes(self) -> None:
        long_root = self.base / "VeyraShortPathProject"
        long_root.mkdir()
        short_root = _short_path(long_root)

        store = ProjectStore(short_root)

        self.assertEqual(store.root, long_root.resolve())
        store.init(name="short path", pack_type="desktop_theme")
        self.assertTrue((long_root / "creation" / "state.json").is_file())
        self.assertEqual(store.load_state()["spec_revision"], 1)

    def test_short_parent_and_missing_suffix_is_accepted(self) -> None:
        long_parent = self.base / "VeyraShortPathParent"
        long_parent.mkdir()
        short_parent = _short_path(long_parent)
        short_root = short_parent / "CreatedFromShortPath"

        store = ProjectStore(short_root)
        store.init(name="short child", pack_type="desktop_theme")

        expected = (long_parent / "CreatedFromShortPath").resolve()
        self.assertEqual(store.root, expected)
        self.assertTrue((expected / "creation" / "state.json").is_file())

    def test_cli_accepts_a_short_root(self) -> None:
        long_root = self.base / "VeyraShortPathCli"
        long_root.mkdir()
        short_root = _short_path(long_root)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "veyra_creation",
                "init",
                str(short_root),
                "--name",
                "short cli",
                "--pack-type",
                "desktop_theme",
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue((long_root / "creation" / "state.json").is_file())

    def test_case_alternate_spelling_refers_to_the_same_root(self) -> None:
        long_root = self.base / "CaseSensitiveProject"
        long_root.mkdir()
        alternate = self.base / "casesensitiveproject"

        store = ProjectStore(alternate)

        self.assertTrue(os.path.samefile(store.root, long_root))

    def test_similar_names_are_not_treated_as_the_same_root(self) -> None:
        first = self.base / "project-one"
        second = self.base / "project-two"
        first.mkdir()
        second.mkdir()

        self.assertNotEqual(ProjectStore(first).root, ProjectStore(second).root)


@unittest.skipUnless(os.name == "nt", "Windows long-path binding tests")
class LongPathBindingTests(unittest.TestCase):
    def test_success_uses_the_fixed_buffer(self) -> None:
        fake = _FakeLongPathFunction(32767, value=r"C:\long\project")
        with mock.patch(
            "veyra_pack.paths._get_long_path_name_w", return_value=fake
        ):
            value, error = _windows_long_name(Path(r"C:\short"))

        self.assertEqual((value, error), (r"C:\long\project", 0))
        self.assertEqual(fake.calls, [(r"C:\short", 32768)])

    def test_exact_capacity_is_rejected_without_retry(self) -> None:
        fake = _FakeLongPathFunction(32768)
        with mock.patch(
            "veyra_pack.paths._get_long_path_name_w", return_value=fake
        ):
            value, error = _windows_long_name(Path(r"C:\short"))

        self.assertEqual((value, error), (None, 206))
        self.assertEqual(len(fake.calls), 1)

    def test_larger_than_capacity_is_rejected_without_retry(self) -> None:
        fake = _FakeLongPathFunction(40000)
        with mock.patch(
            "veyra_pack.paths._get_long_path_name_w", return_value=fake
        ):
            value, error = _windows_long_name(Path(r"C:\short"))

        self.assertEqual((value, error), (None, 206))
        self.assertEqual(len(fake.calls), 1)

    def test_zero_return_reports_the_actual_win32_error(self) -> None:
        fake = _FakeLongPathFunction(0, error=5)
        with mock.patch(
            "veyra_pack.paths._get_long_path_name_w", return_value=fake
        ):
            value, error = _windows_long_name(Path(r"C:\short"))

        self.assertEqual((value, error), (None, 5))

    def test_missing_binding_fails_closed(self) -> None:
        with mock.patch("veyra_pack.paths._get_long_path_name_w", return_value=None):
            value, error = _windows_long_name(Path(r"C:\short"))

        self.assertEqual((value, error), (None, 0))

    def test_binding_failure_is_cached(self) -> None:
        previous = paths_module._GET_LONG_PATH_NAME_W
        paths_module._GET_LONG_PATH_NAME_W = None
        self.addCleanup(
            setattr, paths_module, "_GET_LONG_PATH_NAME_W", previous
        )
        with mock.patch.object(
            paths_module.ctypes, "WinDLL", side_effect=OSError("no kernel32")
        ) as loader:
            self.assertIsNone(paths_module._get_long_path_name_w())
            self.assertIsNone(paths_module._get_long_path_name_w())

        self.assertEqual(loader.call_count, 1)

    def test_successful_binding_is_cached(self) -> None:
        previous = paths_module._GET_LONG_PATH_NAME_W
        paths_module._GET_LONG_PATH_NAME_W = None
        self.addCleanup(
            setattr, paths_module, "_GET_LONG_PATH_NAME_W", previous
        )
        fake = _FakeLongPathFunction(32767, value=r"C:\long\project")
        library = SimpleNamespace(GetLongPathNameW=fake)
        with mock.patch.object(
            paths_module.ctypes, "WinDLL", return_value=library
        ) as loader:
            first = _windows_long_name(Path(r"C:\first"))
            second = _windows_long_name(Path(r"C:\second"))
            self.assertIs(paths_module._get_long_path_name_w(), fake)

        self.assertEqual(
            (first, second),
            ((r"C:\long\project", 0), (r"C:\long\project", 0)),
        )
        loader.assert_called_once_with("kernel32", use_last_error=True)
        self.assertEqual(
            fake.calls,
            [(r"C:\first", 32768), (r"C:\second", 32768)],
        )

    def test_invocation_failure_fails_closed(self) -> None:
        fake = _FakeLongPathFunction(0, raises=OSError("boom"))
        with mock.patch(
            "veyra_pack.paths._get_long_path_name_w", return_value=fake
        ):
            value, error = _windows_long_name(Path(r"C:\short"))

        self.assertEqual((value, error), (None, 0))


@unittest.skipUnless(os.name == "nt", "Windows alias traversal tests")
class AliasTraversalTests(unittest.TestCase):
    def test_missing_suffix_ascent_uses_only_errors_2_and_3(self) -> None:
        base = Path(r"C:\veyra-alias-base")
        target = base / "missing" / "child"

        def fake(path: Path):
            if path == target:
                return None, 2
            if path == target.parent:
                return None, 3
            if path == base:
                return str(base), 0
            return None, 5

        with mock.patch("veyra_pack.paths._windows_long_name", side_effect=fake):
            self.assertEqual(_canonical_alias_path(target), target)

    def test_other_errors_do_not_ascend(self) -> None:
        target = Path(r"C:\veyra-alias-base") / "missing" / "child"
        with mock.patch(
            "veyra_pack.paths._windows_long_name", return_value=(None, 5)
        ):
            self.assertIsNone(_canonical_alias_path(target))


@unittest.skipUnless(os.name == "nt", "Windows reparse-point tests")
class ReparsePointTests(unittest.TestCase):
    def test_generic_reparse_attribute_is_link_like(self) -> None:
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(
            st_mode=stat.S_IFREG,
            st_file_attributes=0x400,
        )

        self.assertTrue(is_link_like(path))

    def test_missing_windows_attribute_fails_closed(self) -> None:
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFREG)

        self.assertTrue(is_link_like(path))

    def test_lstat_failure_is_link_like(self) -> None:
        path = mock.Mock()
        path.lstat.side_effect = PermissionError("denied")

        self.assertTrue(is_link_like(path))

    def test_junction_is_rejected_by_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            outside = base / "outside"
            outside.mkdir()
            link = base / "junction"
            if not _make_junction(link, outside):
                self.skipTest("junction creation is not available")

            self.assertTrue(is_link_like(link))
            with self.assertRaises(CreationError) as caught:
                ProjectStore(link)
            self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")


@unittest.skipUnless(os.name == "nt", "Windows root equivalence tests")
class RootEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)

    def test_existing_short_alias_is_equivalent(self) -> None:
        long_root = self.base / "VeyraRootEquivalence"
        long_root.mkdir()
        short_root = _short_path(long_root)

        self.assertTrue(_root_paths_equivalent(short_root, long_root.resolve()))

    def test_missing_short_alias_is_equivalent(self) -> None:
        long_parent = self.base / "VeyraRootEquivalenceParent"
        long_parent.mkdir()
        short_parent = _short_path(long_parent)
        missing = short_parent / "MissingRoot"

        self.assertTrue(
            _root_paths_equivalent(missing, (long_parent / "MissingRoot").resolve())
        )

    def test_samefile_failure_fails_closed(self) -> None:
        long_root = self.base / "VeyraSameFileFailure"
        long_root.mkdir()
        short_root = _short_path(long_root)

        with mock.patch(
            "veyra_pack.paths.os.path.samefile", side_effect=OSError("denied")
        ):
            self.assertFalse(
                _root_paths_equivalent(short_root, long_root.resolve())
            )

    def test_alias_failure_fails_closed(self) -> None:
        with mock.patch(
            "veyra_pack.paths._canonical_alias_path", return_value=None
        ):
            self.assertFalse(
                _root_paths_equivalent(
                    self.base / "short", self.base / "long"
                )
            )

    def test_concrete_alias_mismatch_and_prefix_collision_fail_closed(self) -> None:
        previous = paths_module._GET_LONG_PATH_NAME_W
        self.addCleanup(
            setattr, paths_module, "_GET_LONG_PATH_NAME_W", previous
        )

        def assert_rejected(
            declared: Path, resolved: Path, canonical: Path
        ) -> None:
            paths_module._GET_LONG_PATH_NAME_W = None
            fake = _FakeLongPathFunction(32767, value=str(canonical))
            library = SimpleNamespace(GetLongPathNameW=fake)
            with mock.patch.object(
                paths_module.ctypes, "WinDLL", return_value=library
            ) as loader:
                self.assertFalse(_root_paths_equivalent(declared, resolved))
            loader.assert_called_once_with("kernel32", use_last_error=True)
            self.assertEqual(fake.calls, [(str(declared), 32768)])

        project = self.base / "project"
        assert_rejected(
            self.base / "VeyraAliasDeclared",
            self.base / "VeyraAliasResolved",
            self.base / "VeyraAliasDifferent",
        )
        assert_rejected(project, self.base / "project-extra", project)

        self.assertTrue(_root_paths_equivalent(project, project))

    def test_metadata_failure_after_alias_match_fails_closed(self) -> None:
        long_parent = self.base / "VeyraMetadataFailureParent"
        long_parent.mkdir()
        short_parent = _short_path(long_parent)
        declared = short_parent / "MissingRoot"
        resolved = (long_parent / "MissingRoot").resolve()

        with mock.patch(
            "pathlib.Path.lstat", side_effect=PermissionError("denied")
        ):
            self.assertFalse(_root_paths_equivalent(declared, resolved))


class StoreCallOrderTests(unittest.TestCase):
    def test_declared_check_precedes_equivalence_and_resolved_check(self) -> None:
        events: list[str] = []

        def fake_link(path: Path) -> None:
            events.append("link")
            return None

        def fake_equivalent(declared: Path, resolved: Path) -> bool:
            events.append("equivalent")
            return True

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            with mock.patch.object(
                store_module, "find_link_component", side_effect=fake_link
            ), mock.patch.object(
                store_module, "_root_paths_equivalent", side_effect=fake_equivalent
            ):
                ProjectStore(root)

        self.assertEqual(events, ["link", "equivalent", "link"])

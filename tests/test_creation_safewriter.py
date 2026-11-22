"""Real-filesystem tests for the Phase 3.2.2.1-2.4 SafeWriter prototype.

These tests intentionally use a private temporary project root.  They exercise
the handle-anchored create/replace path and the refusals that must happen before
a third-party object can be changed.  They do not test ProjectStore, delete or
recovery; those are outside this phase.
"""

from __future__ import annotations

import ctypes
import gc
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from veyra_creation.safewriter import (
    CreateRequest,
    ExpectedTarget,
    Identity,
    ReplaceRequest,
    RequestRef,
    SafeWriter,
)
from veyra_creation.safewriter.anchors import RootAnchor
from veyra_creation.safewriter.errors import (
    E_COMMIT_UNCERTAIN,
    E_HARDLINK_REFUSED,
    E_IO,
    E_LOCK_CONFLICT,
    E_NOT_IMPLEMENTED,
    E_PATH_INVALID,
    E_REPARSE_REFUSED,
    E_ROOT_CHANGED,
    E_TARGET_ABSENT,
    E_TARGET_CHANGED,
    E_TARGET_EXISTS,
    E_TARGET_UNSUPPORTED,
    SafeWriterError,
)
from veyra_creation.safewriter.lock import LockManager, OwnerInfo
from veyra_creation.safewriter.results import COMPLETED, FAILED, REFUSED, UNCERTAIN
from veyra_creation.safewriter.target import TargetAnchor


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ref(op_id: str = "op1") -> RequestRef:
    return RequestRef("batch1", op_id, "approval-1", "plan-1", "0" * 64, "safewriter test")


def _handle_count() -> int:
    """Return this process's Windows handle count (test-only instrumentation)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetProcessHandleCount.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.GetProcessHandleCount.restype = ctypes.c_bool
    count = ctypes.c_ulong()
    if not kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(count.value)


@unittest.skipUnless(os.name == "nt", "SafeWriter uses Windows native file APIs")
class SafeWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = tempfile.mkdtemp(prefix="veyra-sw-test-")
        self.root = os.path.join(self.base, "project")
        os.mkdir(self.root)
        self._identity = self._root_identity()

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    def _root_identity(self):
        anchor = RootAnchor.open(self.root)
        try:
            return anchor.identity
        finally:
            anchor.close()

    def _writer(self, **kwargs) -> SafeWriter:
        return SafeWriter(self.root, expected_root_identity=self._identity, **kwargs)

    def _expected(self, path: Path) -> ExpectedTarget:
        anchor = RootAnchor.open(self.root)
        parent = None
        target = None
        try:
            relative = Path(path).relative_to(self.root)
            parent = anchor.open_path(relative.parts[:-1], create=False) if relative.parts[:-1] else anchor.final_anchor()
            target = TargetAnchor.open_existing(parent, relative.parts[-1])
            return ExpectedTarget(target.facts.identity, target.facts.sha256)
        finally:
            if target is not None:
                target.close()
            if parent is not None:
                parent.close()
            anchor.close()

    def _journal_events(self, batch_id: str = "batch1") -> list[dict]:
        path = Path(self.root) / "creation" / "operations" / f"{batch_id}.journal.ndjson"
        raw_lines = path.read_bytes().split(b"\n")[:-1]
        events = [json.loads(line) for line in raw_lines]
        previous = None
        for index, (line, event) in enumerate(zip(raw_lines, events), start=1):
            self.assertEqual(event["seq"], index)
            self.assertEqual(event["prev_event_sha256"], previous)
            previous = hashlib.sha256(line).hexdigest()
        return events

    def _handle_delta(self, action, *, iterations: int = 25) -> int:
        """Warm up once, then measure handle growth over repeated attempts."""
        action()
        gc.collect()
        before = _handle_count()
        for _ in range(iterations):
            action()
        gc.collect()
        return _handle_count() - before

    def test_root_expected_identity_mismatch_does_not_leak_handles(self) -> None:
        wrong = replace(self._identity, file_id="0" * 32)

        def attempt() -> None:
            try:
                RootAnchor.open(self.root, expected=wrong)
            except SafeWriterError as exc:
                self.assertEqual(exc.code, E_ROOT_CHANGED)
            else:
                self.fail("mismatched expected root identity was accepted")

        self.assertLessEqual(self._handle_delta(attempt), 4)

    def test_open_path_failure_does_not_leak_ancestor_handles(self) -> None:
        (Path(self.root) / "a").mkdir()

        def attempt() -> None:
            anchor = RootAnchor.open(self.root)
            try:
                try:
                    anchor.open_path(("a", "missing", "child"))
                except SafeWriterError as exc:
                    self.assertEqual(exc.code, E_TARGET_ABSENT)
                else:
                    self.fail("open_path unexpectedly accepted a missing intermediate")
            finally:
                anchor.close()

        self.assertLessEqual(self._handle_delta(attempt), 4)

    def test_create_parent_file_failure_does_not_leak_handles(self) -> None:
        (Path(self.root) / "a").mkdir()
        (Path(self.root) / "a" / "b").write_bytes(b"not a directory")

        def attempt() -> None:
            result = self._writer().create(
                CreateRequest(_ref("parent-fail"), "a/b/c.txt", b"content")
            )
            self.assertEqual((result.status, result.code), (REFUSED, E_TARGET_UNSUPPORTED))

        self.assertLessEqual(self._handle_delta(attempt), 4)

    def test_surrogate_target_name_is_refused_before_session(self) -> None:
        result = self._writer().create(
            CreateRequest(_ref("surrogate-target"), "bad\ud800.txt", b"content")
        )
        self.assertEqual((result.status, result.code), (REFUSED, E_PATH_INVALID))
        self.assertIn("surrogate", result.message)
        self.assertFalse(Path(self.root, "bad\ud800.txt").exists())
        self.assertFalse(Path(self.root, "creation").exists())

    def test_surrogate_parent_path_is_refused_before_session(self) -> None:
        result = self._writer().create(
            CreateRequest(_ref("surrogate-parent"), "ok/bad\ud800/c.txt", b"content")
        )
        self.assertEqual((result.status, result.code), (REFUSED, E_PATH_INVALID))
        self.assertIn("surrogate", result.message)
        self.assertFalse(Path(self.root, "ok").exists())
        self.assertFalse(Path(self.root, "creation").exists())

    def test_surrogate_journal_ref_is_refused_before_session(self) -> None:
        ref = RequestRef("batch1", "surrogate-ref", "approval-1", "plan-1", "0" * 64, "bad\ud800")
        result = self._writer().create(CreateRequest(ref, "a.txt", b"content"))
        self.assertEqual((result.status, result.code), (REFUSED, E_PATH_INVALID))
        self.assertIn("surrogate", result.message)
        self.assertFalse(Path(self.root, "creation").exists())

    def test_create_completes_with_real_content_and_journal_chain(self) -> None:
        result = self._writer().create(CreateRequest(_ref("c1"), "sub/a.txt", b"hello"))
        self.assertEqual(result.status, COMPLETED)
        self.assertIsNone(result.code)
        self.assertEqual(result.sha256, _sha(b"hello"))
        self.assertEqual(Path(self.root, "sub", "a.txt").read_bytes(), b"hello")
        events = self._journal_events()
        self.assertEqual(
            [event["state"] for event in events],
            [
                "PARENT_DIRS_INTENT",
                "PARENT_READY",
                "TARGET_ABSENT",
                "CREATE_INTENT",
                "TEMP_READY",
                "COMMIT_INTENT",
                "COMMITTED",
            ],
        )
        # The temp directory is not left behind once the target name exists.
        self.assertFalse(list(Path(self.root, "sub").glob(".veyra-*.tmp")))

    def test_non_bmp_root_parent_and_target_are_not_truncated(self) -> None:
        sibling = Path(self.base, "proj😀")
        sibling.mkdir()
        root = Path(self.base, "proj😀x")
        root.mkdir()
        (root / "sub😀").mkdir()
        (root / "sub😀" / "a😀").write_bytes(b"other")
        result = SafeWriter(str(root)).create(
            CreateRequest(_ref("emoji"), "sub😀/a😀.txt", b"emoji")
        )
        self.assertEqual(result.status, COMPLETED)
        self.assertEqual((root / "sub😀" / "a😀.txt").read_bytes(), b"emoji")
        self.assertEqual((root / "sub😀" / "a😀").read_bytes(), b"other")
        self.assertFalse((sibling / "sub😀").exists())

    def test_create_uncertain_commit_preserves_temp_and_target_absence(self) -> None:
        def uncertain_rename(*_args, **_kwargs):
            raise SafeWriterError(E_COMMIT_UNCERTAIN, "simulated uncertain rename")

        with mock.patch.object(SafeWriter, "_rename", side_effect=uncertain_rename):
            result = self._writer().create(CreateRequest(_ref("c1"), "a.txt", b"x"))

        self.assertEqual((result.status, result.code), (UNCERTAIN, E_COMMIT_UNCERTAIN))
        self.assertFalse(Path(self.root, "a.txt").exists())
        temps = list(Path(self.root).glob(".veyra-*.tmp"))
        self.assertEqual(len(temps), 1)
        self.assertEqual(self._journal_events()[-1]["state"], "UNCERTAIN")
        # The retained temp must not keep a leaked write/DELETE handle open.
        temps[0].unlink()
        self.assertFalse(temps[0].exists())

    def test_temp_write_oserror_returns_structured_failure(self) -> None:
        real_write_all = __import__(
            "veyra_creation.safewriter.writer", fromlist=["native"]
        ).native.write_all
        temp_handles: list[int] = []
        temp_files_at_failure: list[str] = []

        def fail_only_temp_write(handle, data, *, truncate=True):
            # Lock owner metadata also uses native.write_all.  Trigger only on
            # the actual temp content write, after the temp file exists.
            if data == b"payload" and truncate:
                temp_handles.append(handle)
                temp_files_at_failure.extend(
                    path.name for path in Path(self.root).glob(".veyra-*.tmp")
                )
                raise OSError("simulated disk full during temp write")
            return real_write_all(handle, data, truncate=truncate)

        with mock.patch(
            "veyra_creation.safewriter.writer.native.write_all",
            side_effect=fail_only_temp_write,
        ):
            result = self._writer().create(CreateRequest(_ref("c1"), "a.txt", b"payload"))

        self.assertTrue(temp_handles, "the injected failure did not reach the temp write")
        self.assertTrue(temp_files_at_failure, "no temp file existed at the injected failure")
        self.assertEqual((result.status, result.code), (FAILED, E_IO))
        self.assertIn("temp write/readback failed", result.message)
        self.assertTrue(result.details["journal_recorded"])
        self.assertFalse(Path(self.root, "a.txt").exists())
        self.assertFalse(list(Path(self.root).glob(".veyra-*.tmp")))
        events = self._journal_events()
        states = [event["state"] for event in events]
        self.assertNotIn("TEMP_READY", states)
        self.assertEqual(states[-1], "FAILED")
        self.assertEqual(events[-1]["error_code"], E_IO)

    def test_partial_journal_tail_is_repaired_before_next_operation(self) -> None:
        first = self._writer().create(CreateRequest(_ref("c1"), "a.txt", b"first"))
        self.assertEqual(first.status, COMPLETED)
        journal_path = Path(self.root, "creation", "operations", "batch1.journal.ndjson")
        with journal_path.open("ab") as stream:
            stream.write(b'{"torn":')
        second = self._writer().create(CreateRequest(_ref("c2"), "b.txt", b"second"))
        self.assertEqual(second.status, COMPLETED)
        self.assertTrue(journal_path.read_bytes().endswith(b"\n"))
        events = self._journal_events()
        self.assertIn("JOURNAL_TAIL_REPAIRED", [event["state"] for event in events])
        self.assertEqual(events[-1]["op_id"], "c2")
        self.assertEqual(events[-1]["state"], "COMMITTED")

    def test_replace_completes_and_preserves_original_in_quarantine(self) -> None:
        original = Path(self.root, "a.txt")
        original.write_bytes(b"old")
        writer = self._writer()
        result = writer.replace(
            ReplaceRequest(_ref("r1"), "a.txt", b"new", self._expected(original))
        )
        self.assertEqual(result.status, COMPLETED)
        self.assertEqual(original.read_bytes(), b"new")
        quarantine = Path(self.root, "creation", "quarantine", "batch1", "r1", "r1.quarantine")
        self.assertEqual(quarantine.read_bytes(), b"old")
        events = self._journal_events()
        states = [event["state"] for event in events]
        self.assertIn("TARGET_VERIFIED", states)
        self.assertIn("QUARANTINED", states)
        self.assertEqual(states[-1], "COMMITTED")

    def test_create_refuses_existing_target_without_touching_it(self) -> None:
        target = Path(self.root, "exists.txt")
        target.write_bytes(b"old")
        result = self._writer().create(CreateRequest(_ref("c1"), "exists.txt", b"new"))
        self.assertEqual((result.status, result.code), (REFUSED, E_TARGET_EXISTS))
        self.assertEqual(target.read_bytes(), b"old")

    def test_replace_refuses_missing_target(self) -> None:
        result = self._writer().replace(
            ReplaceRequest(
                _ref("r1"),
                "missing.txt",
                b"new",
                ExpectedTarget(Identity(self._identity.volume_serial, "0" * 32), _sha(b"old")),
            )
        )
        self.assertEqual((result.status, result.code), (REFUSED, E_TARGET_ABSENT))

    def test_replace_refuses_identity_mismatch_even_with_same_hash(self) -> None:
        target = Path(self.root, "a.txt")
        target.write_bytes(b"same")
        wrong = ExpectedTarget(Identity(self._identity.volume_serial, "0" * 32), _sha(b"same"))
        result = self._writer().replace(ReplaceRequest(_ref("r1"), "a.txt", b"new", wrong))
        self.assertEqual((result.status, result.code), (REFUSED, E_TARGET_CHANGED))
        self.assertEqual(target.read_bytes(), b"same")

    def test_replace_refuses_hash_mismatch(self) -> None:
        target = Path(self.root, "a.txt")
        target.write_bytes(b"same")
        expected = self._expected(target)
        wrong = ExpectedTarget(expected.identity, _sha(b"different"))
        result = self._writer().replace(ReplaceRequest(_ref("r1"), "a.txt", b"new", wrong))
        self.assertEqual((result.status, result.code), (REFUSED, E_TARGET_CHANGED))
        self.assertEqual(target.read_bytes(), b"same")

    def test_replace_refuses_hardlinked_target(self) -> None:
        target = Path(self.root, "a.txt")
        target.write_bytes(b"old")
        os.link(target, Path(self.root, "alias.txt"))
        result = self._writer().replace(
            ReplaceRequest(
                _ref("r1"),
                "a.txt",
                b"new",
                ExpectedTarget(Identity(self._identity.volume_serial, "0" * 32), _sha(b"old")),
            )
        )
        self.assertEqual((result.status, result.code), (REFUSED, E_HARDLINK_REFUSED))
        self.assertEqual(target.read_bytes(), b"old")
        self.assertEqual(Path(self.root, "alias.txt").read_bytes(), b"old")

    def test_lock_conflict_refuses_before_writing(self) -> None:
        anchor = RootAnchor.open(self.root)
        try:
            creation = anchor.open_child_dir("creation", create=True)
            try:
                owner = OwnerInfo(
                    owner_token="test-owner",
                    pid=os.getpid(),
                    batch_id="batch1",
                    root_identity=anchor.identity.as_dict(),
                    host="test",
                    tool_version="test",
                    acquired_at="2026-01-01T00:00:00Z",
                )
                lock = LockManager.acquire(creation, owner=owner)
                try:
                    result = self._writer().create(CreateRequest(_ref("c1"), "a.txt", b"x"))
                finally:
                    lock.release()
            finally:
                creation.close()
        finally:
            anchor.close()
        self.assertEqual((result.status, result.code), (REFUSED, E_LOCK_CONFLICT))
        self.assertFalse(Path(self.root, "a.txt").exists())

    def test_root_replacement_before_call_is_refused(self) -> None:
        old_identity = self._identity
        os.rename(self.root, self.root + "-moved")
        os.mkdir(self.root)
        result = SafeWriter(self.root, expected_root_identity=old_identity).create(
            CreateRequest(_ref("c1"), "a.txt", b"x")
        )
        self.assertEqual((result.status, result.code), (REFUSED, E_ROOT_CHANGED))
        self.assertEqual(list(Path(self.root).iterdir()), [])

    def test_junction_parent_is_refused_and_not_followed(self) -> None:
        outside = Path(self.base, "outside")
        outside.mkdir()
        junction = Path(self.root, "junction")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            self.skipTest(f"cannot create junction: {completed.stderr or completed.stdout}")
        try:
            result = self._writer().create(CreateRequest(_ref("c1"), "junction/a.txt", b"x"))
            self.assertEqual((result.status, result.code), (REFUSED, E_REPARSE_REFUSED))
            self.assertFalse((outside / "a.txt").exists())
        finally:
            os.rmdir(junction)

    def test_junction_project_root_is_refused(self) -> None:
        actual = Path(self.base, "actual")
        actual.mkdir()
        link = Path(self.base, "linked-project")
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(actual)],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            self.skipTest(f"cannot create junction: {completed.stderr or completed.stdout}")
        try:
            result = SafeWriter(str(link), expected_root_identity=None).create(
                CreateRequest(_ref("c1"), "a.txt", b"x")
            )
            self.assertEqual((result.status, result.code), (REFUSED, E_REPARSE_REFUSED))
            self.assertFalse((actual / "a.txt").exists())
        finally:
            os.rmdir(link)

    def test_create_race_target_appearing_after_temp_is_preserved(self) -> None:
        target = Path(self.root, "race.txt")

        def hook(stage: str, _info: dict) -> None:
            if stage == "create:temp_ready":
                target.write_bytes(b"third-party")

        result = self._writer(fault_hook=hook).create(
            CreateRequest(_ref("c1"), "race.txt", b"writer")
        )
        self.assertEqual((result.status, result.code), (FAILED, E_TARGET_EXISTS))
        self.assertEqual(target.read_bytes(), b"third-party")
        self.assertFalse(list(Path(self.root).glob(".veyra-*.tmp")))

    def test_ancestor_junction_inserted_before_call_is_refused(self) -> None:
        outside = Path(self.base, "outside-ancestor")
        outside.mkdir()
        parent = Path(self.root, "parent")
        parent.mkdir()
        os.rmdir(parent)
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(parent), str(outside)],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            self.skipTest(f"cannot create junction: {completed.stderr or completed.stdout}")
        try:
            result = self._writer().create(CreateRequest(_ref("c1"), "parent/a.txt", b"x"))
            self.assertEqual((result.status, result.code), (REFUSED, E_REPARSE_REFUSED))
            self.assertFalse((outside / "a.txt").exists())
        finally:
            os.rmdir(parent)

    def test_delete_and_recover_are_explicitly_not_implemented(self) -> None:
        writer = self._writer()
        delete_result = writer.delete(object())
        recover_result = writer.recover(object())
        self.assertEqual((delete_result.status, delete_result.code), (REFUSED, E_NOT_IMPLEMENTED))
        self.assertEqual((recover_result.status, recover_result.code), (REFUSED, E_NOT_IMPLEMENTED))

    def test_invalid_relative_path_is_refused_before_session(self) -> None:
        result = self._writer().create(CreateRequest(_ref("c1"), "../escape.txt", b"x"))
        self.assertEqual((result.status, result.code), (REFUSED, E_PATH_INVALID))
        self.assertFalse(Path(self.base, "escape.txt").exists())

    def test_creation_internal_path_is_refused(self) -> None:
        result = self._writer().create(CreateRequest(_ref("c1"), "creation/state.json", b"x"))
        self.assertEqual((result.status, result.code), (REFUSED, E_PATH_INVALID))


if __name__ == "__main__":
    unittest.main()

"""Tests for the deterministic Project Digest and ``verify``."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from creation_helpers import (
    add_deliverables,
    confirmed_with_deliverables,
    make_store,
    plan_payload,
    write_file,
)

from veyra_pack.contract import write_json_atomic
from veyra_creation.digest import build_digest, verify_project
from veyra_creation.store import CreationError, ProjectStore


def _make_symlink(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        raise unittest.SkipTest(f"cannot create a symlink here: {exc}") from exc


def _make_junction(link: Path, target: Path) -> None:
    if os.name != "nt":
        raise unittest.SkipTest("directory junctions are only available on Windows")
    try:
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise unittest.SkipTest(f"cannot invoke mklink here: {exc}") from exc
    if result.returncode != 0:
        raise unittest.SkipTest(
            f"cannot create a directory junction here: {result.stderr.strip() or result.stdout.strip()}"
        )


class DigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "project"
        self.store = make_store(self.root)

    def test_digest_is_deterministic(self) -> None:
        write_file(self.root, "assets/wallpaper.png", b"pixels")
        add_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper.png", "kind": "image"},
        )
        first = build_digest(self.store)
        second = build_digest(self.store)
        self.assertEqual(first["digest_id"], second["digest_id"])
        self.assertEqual(first["files"], second["files"])
        self.assertEqual(first["file_count"], 1)
        self.assertEqual(first["total_bytes"], len(b"pixels"))

    def test_digest_excludes_state_and_protected_files(self) -> None:
        write_file(self.root, "assets/ink.png", b"ink")
        write_file(self.root, "real_crash.log", b"do not touch")
        write_file(self.root, ".git/config", b"secret")
        digest = build_digest(self.store)
        paths = {item["path"] for item in digest["files"]}
        self.assertIn("assets/ink.png", paths)
        self.assertNotIn("real_crash.log", paths)
        self.assertNotIn(".git/config", paths)
        self.assertFalse(any(path.startswith("creation/") for path in paths))

    def test_missing_required_deliverable_warns(self) -> None:
        add_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/missing.png", "kind": "image"},
        )
        digest = build_digest(self.store)
        self.assertEqual(digest["deliverables"][0]["status"], "missing")
        codes = {item["code"] for item in digest["warnings"]}
        self.assertIn("W_DELIVERABLE_MISSING", codes)

    def test_optional_missing_deliverable_does_not_warn(self) -> None:
        add_deliverables(
            self.store,
            {
                "id": "trailer",
                "path": "assets/trailer.png",
                "kind": "image",
                "optional": True,
            },
        )
        digest = build_digest(self.store)
        codes = {item["code"] for item in digest["warnings"]}
        self.assertNotIn("W_DELIVERABLE_MISSING", codes)

    def test_digest_id_ignores_the_generation_time(self) -> None:
        self.store.clock = lambda: "2026-11-21T00:00:00Z"
        first = build_digest(self.store)
        self.store.clock = lambda: "2030-01-01T00:00:00Z"
        second = build_digest(self.store)
        self.assertEqual(first["digest_id"], second["digest_id"])
        self.assertNotEqual(first["generated_at"], second["generated_at"])

    def test_digest_changes_when_a_file_changes(self) -> None:
        write_file(self.root, "assets/ink.png", b"v1")
        before = build_digest(self.store)["digest_id"]
        write_file(self.root, "assets/ink.png", b"v2")
        after = build_digest(self.store)["digest_id"]
        self.assertNotEqual(before, after)

    def test_written_digest_round_trips_and_has_no_stale_temp_files(self) -> None:
        digest = build_digest(self.store, write=True)
        saved = json.loads(self.store.digest_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["digest_id"], digest["digest_id"])
        self.assertEqual(build_digest(self.store)["digest_id"], digest["digest_id"])
        leftovers = list(self.store.creation_dir.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_digest_records_history_and_runs(self) -> None:
        run = self.store.start_run("generate wallpaper")
        self.store.finish_run(run["run_id"], status="ok")
        digest = build_digest(self.store)
        self.assertEqual(digest["history"]["decision_count"], 1)
        self.assertEqual(len(digest["spec"]["sha256"]), 64)
        self.assertEqual(digest["runs"]["count"], 1)
        self.assertEqual(digest["runs"]["last_status"], "ok")
        self.assertEqual(digest["runs"]["items"][0]["run_id"], run["run_id"])
        self.assertEqual(digest["runs"]["items"][0]["status"], "ok")

    def test_state_survives_reopening_the_project(self) -> None:
        write_file(self.root, "assets/avatar.png", b"v1")
        add_deliverables(
            self.store,
            {"id": "avatar", "path": "assets/avatar.png", "kind": "image"},
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        self.store.confirm_spec(2)
        plan = self.store.save_plan(plan_payload(2))
        self.store.approve_plan(plan["plan_id"])
        self.store.lock("assets/avatar.png")
        run = self.store.start_run("generate avatar")
        self.store.finish_run(run["run_id"], status="ok")
        before = build_digest(self.store, write=True)["digest_id"]

        reopened = ProjectStore(self.root)
        self.assertEqual(reopened.load_state()["project_id"], self.store.load_state()["project_id"])
        self.assertEqual(reopened.load_spec()["revision"], 2)
        self.assertEqual(reopened.load_plan()["plan_id"], plan["plan_id"])
        self.assertEqual(reopened.load_locks()[0]["path"], "assets/avatar.png")
        self.assertEqual(reopened.list_runs()[0]["run_id"], run["run_id"])
        self.assertEqual(build_digest(reopened)["digest_id"], before)
        self.assertEqual([issue.format() for issue in verify_project(reopened) if issue.is_error], [])

    def test_digest_is_rebuilt_after_deleting_the_cache(self) -> None:
        build_digest(self.store, write=True)
        before = self.store.digest_path.read_text(encoding="utf-8")
        self.store.digest_path.unlink()
        rebuilt = build_digest(self.store, write=True)
        self.assertEqual(
            json.loads(before)["digest_id"], rebuilt["digest_id"]
        )

    def test_write_false_does_not_touch_an_existing_digest(self) -> None:
        self.store.digest_path.write_text("sentinel", encoding="utf-8")
        build_digest(self.store, write=False)
        self.assertEqual(
            self.store.digest_path.read_text(encoding="utf-8"), "sentinel"
        )
        self.assertEqual(list(self.store.creation_dir.glob("*.tmp")), [])

    def test_write_rejects_a_digest_symlink_without_touching_its_target(self) -> None:
        outside = Path(self._temp.name) / "outside"
        outside.mkdir()
        target = outside / "digest.json"
        target.write_text("original", encoding="utf-8")
        _make_symlink(self.store.digest_path, target)

        with self.assertRaises(CreationError) as caught:
            build_digest(self.store, write=True)

        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertEqual(caught.exception.location, "digest")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.store.creation_dir.glob("*.tmp")), [])

    def test_write_rejects_a_digest_junction_without_touching_its_target(self) -> None:
        outside = Path(self._temp.name) / "outside-digest"
        outside.mkdir()
        target = outside / "digest.json"
        target.write_text("original", encoding="utf-8")
        _make_junction(self.store.digest_path, outside)

        with self.assertRaises(CreationError) as caught:
            build_digest(self.store, write=True)

        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertEqual(caught.exception.location, "digest")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.store.creation_dir.glob("*.tmp")), [])

    def test_write_rejects_a_linked_creation_parent(self) -> None:
        outside = Path(self._temp.name) / "outside-creation"
        shutil.move(str(self.store.creation_dir), str(outside))
        _make_junction(self.store.creation_dir, outside)
        self.addCleanup(os.rmdir, self.store.creation_dir)
        digest = outside / "digest.json"
        digest.write_text("original", encoding="utf-8")

        with self.assertRaises(CreationError) as caught:
            build_digest(self.store, write=True)

        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertEqual(digest.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(outside.glob("*.tmp")), [])


class VerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "project"
        self.store = make_store(self.root)

    def test_clean_project_has_no_errors(self) -> None:
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        self.store.save_plan(plan_payload(revision))
        self.store.approve_plan("plan_0001")
        build_digest(self.store, write=True)
        issues = verify_project(self.store)
        errors = [issue for issue in issues if issue.is_error]
        self.assertEqual([issue.format() for issue in errors], [])

    def test_clean_project_with_a_run_has_no_errors(self) -> None:
        run = self.store.start_run("generate wallpaper")
        self.store.finish_run(run["run_id"], status="ok")
        build_digest(self.store, write=True)
        issues = verify_project(self.store)
        errors = [issue.format() for issue in issues if issue.is_error]
        self.assertEqual(errors, [])

    def test_verify_detects_a_broken_lock(self) -> None:
        write_file(self.root, "assets/avatar.png", b"v1")
        self.store.lock("assets/avatar.png")
        write_file(self.root, "assets/avatar.png", b"v2")
        codes = {issue.code for issue in verify_project(self.store)}
        self.assertIn("E_LOCK_DRIFT", codes)

    def test_verify_detects_a_missing_locked_file(self) -> None:
        path = write_file(self.root, "assets/avatar.png", b"v1")
        self.store.lock("assets/avatar.png")
        path.unlink()
        codes = {issue.code for issue in verify_project(self.store)}
        self.assertIn("E_LOCK_DRIFT", codes)

    def test_verify_detects_state_desync(self) -> None:
        state = self.store.load_state()
        state["spec_revision"] = 99
        write_json_atomic(self.store.state_path, state)
        codes = {issue.code for issue in verify_project(self.store)}
        self.assertIn("E_STATE_DESYNC", codes)

    def test_verify_ignores_a_missing_optional_deliverable(self) -> None:
        add_deliverables(
            self.store,
            {
                "id": "trailer",
                "path": "assets/trailer.png",
                "kind": "image",
                "optional": True,
            },
        )
        codes = {issue.code for issue in verify_project(self.store)}
        self.assertNotIn("W_DELIVERABLE_MISSING", codes)

    def test_verify_reports_a_corrupt_state_file(self) -> None:
        self.store.state_path.write_text("{broken", encoding="utf-8")
        codes = {issue.code for issue in verify_project(self.store)}
        self.assertIn("E_STATE_CORRUPT", codes)

    def test_verify_reports_a_corrupt_spec_file(self) -> None:
        self.store.spec_path.write_text("{broken", encoding="utf-8")
        codes = {issue.code for issue in verify_project(self.store)}
        self.assertIn("E_SPEC_CORRUPT", codes)

    def test_verify_reports_a_corrupt_digest_file(self) -> None:
        self.store.digest_path.write_text("{broken", encoding="utf-8")
        codes = {issue.code for issue in verify_project(self.store)}
        self.assertIn("E_DIGEST_CORRUPT", codes)

    def test_verify_detects_a_stale_digest(self) -> None:
        build_digest(self.store, write=True)
        self.store.append_decision("note", text="after digest")
        codes = {issue.code for issue in verify_project(self.store)}
        self.assertIn("E_DIGEST_STALE", codes)

    def test_verify_detects_a_corrupt_run(self) -> None:
        self.store.runs_dir.mkdir(parents=True, exist_ok=True)
        (self.store.runs_dir / "run_bad.json").write_text("{broken", encoding="utf-8")
        codes = {issue.code for issue in verify_project(self.store)}
        self.assertIn("E_RUN_CORRUPT", codes)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are not supported")
    def test_digest_skips_symlinks(self) -> None:
        target = self.root / "real.txt"
        target.write_text("outside", encoding="utf-8")
        link = self.root / "assets" / "link.txt"
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"cannot create a symlink here: {exc}")
        paths = {item["path"] for item in build_digest(self.store)["files"]}
        self.assertIn("real.txt", paths)
        self.assertNotIn("assets/link.txt", paths)


if __name__ == "__main__":
    unittest.main()

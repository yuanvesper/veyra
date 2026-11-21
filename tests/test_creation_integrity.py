"""Negative tests for the creation store's integrity boundaries.

These tests deliberately corrupt files in a temporary project and check that
``verify`` fails closed: a deleted approval, an edited Spec/Plan, a bad run, a
broken placeholder file or a legacy history must never be reported as healthy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

from creation_helpers import (
    add_deliverables,
    confirmed_with_deliverables,
    make_store,
    plan_payload,
    write_file,
)

from veyra_pack.contract import write_json_atomic
from veyra_creation.canonical import sha256_file
from veyra_creation.digest import build_digest, verify_project
from veyra_creation.errors import CreationError
from veyra_creation.store import ProjectStore, _plan_content_sha


def _codes(store: ProjectStore) -> set[str]:
    return {issue.code for issue in verify_project(store)}


def _rewrite(path: Path, payload: dict) -> None:
    write_json_atomic(path, payload)


def _make_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and os.path.isjunction(link)


def _append_event(store: ProjectStore, action: str, **fields: Any) -> dict[str, Any]:
    """Append a correctly linked raw event, bypassing Store's API guards."""

    events = store.load_decisions()
    event: dict[str, Any] = {
        "event_id": "dec_" + uuid.uuid4().hex[:12],
        "ts": "2026-11-21T00:00:00Z",
        "actor": "system",
        "action": action,
        "event_format": 2,
        "prev_event_id": events[-1]["event_id"] if events else None,
    }
    event.update(fields)
    with store.decisions_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    state = store.load_state()
    state["last_event_id"] = event["event_id"]
    _rewrite(store.state_path, state)
    return event


class ApprovalIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "project"
        self.store = make_store(self.root)

    def _approved(self) -> tuple[dict, dict]:
        write_file(self.root, "assets/wallpaper/ink.png", b"ink")
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        plan = self.store.save_plan(plan_payload(revision))
        self.store.approve_plan(plan["plan_id"])
        return plan, revision

    def test_deleted_approval_event_is_detected(self) -> None:
        self._approved()
        lines = self.store.decisions_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[-1])["action"], "approve_plan")
        self.store.decisions_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

        codes = _codes(self.store)
        self.assertIn("E_STATE_DESYNC", codes)
        self.assertIn("E_PLAN_TAMPERED", codes)

    def test_empty_decision_log_is_detected(self) -> None:
        self._approved()
        self.store.decisions_path.write_text("", encoding="utf-8")
        self.assertIn("E_DECISIONS_CORRUPT", _codes(self.store))

    def test_edited_confirmed_spec_is_detected(self) -> None:
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        self.assertGreaterEqual(revision, 2)
        spec = self.store.load_spec()
        spec["project"]["name"] = "edited after confirmation"
        _rewrite(self.store.spec_path, spec)
        self.assertIn("E_SPEC_TAMPERED", _codes(self.store))

    def test_edited_approved_plan_is_detected(self) -> None:
        plan, _ = self._approved()
        plan["steps"][0]["target"] = "assets/elsewhere.png"
        _rewrite(self.store.plan_path, plan)
        self.assertIn("E_PLAN_TAMPERED", _codes(self.store))

    def test_approve_requires_an_active_plan(self) -> None:
        write_file(self.root, "assets/wallpaper/ink.png", b"ink")
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        self.store.save_plan(plan_payload(revision))
        state = self.store.load_state()
        state["active_plan_id"] = None
        _rewrite(self.store.state_path, state)
        with self.assertRaises(CreationError) as caught:
            self.store.approve_plan("plan_0001")
        self.assertEqual(caught.exception.code, "E_STATE_DESYNC")

    def test_legacy_history_is_rejected_and_reportable(self) -> None:
        self._approved()
        lines = []
        for line in self.store.decisions_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            event.pop("event_format", None)
            event.pop("prev_event_id", None)
            lines.append(json.dumps(event, sort_keys=True, separators=(",", ":")))
        self.store.decisions_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        self.assertIn("E_DECISIONS_LEGACY", _codes(self.store))
        with self.assertRaises(CreationError) as caught:
            self.store.append_decision("note", text="cannot append")
        self.assertEqual(caught.exception.code, "E_DECISIONS_LEGACY")


class LifecycleIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "project"
        self.store = make_store(self.root)

    def _confirmed(self) -> int:
        write_file(self.root, "assets/wallpaper/ink.png", b"ink")
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        return int(revision)

    def _approved(self) -> tuple[dict[str, Any], int]:
        revision = self._confirmed()
        plan = self.store.save_plan(plan_payload(revision))
        self.store.approve_plan(plan["plan_id"])
        return plan, revision

    def test_duplicate_confirm_is_rejected_even_with_valid_chain(self) -> None:
        revision = self._confirmed()
        spec = self.store.load_spec()
        spec["project"]["name"] = "forged after confirmation"
        _rewrite(self.store.spec_path, spec)

        before = len(self.store.load_decisions())
        with self.assertRaises(CreationError) as caught:
            self.store.confirm_spec(revision)  # idempotent only for an untampered Spec
        self.assertEqual(caught.exception.code, "E_SPEC_TAMPERED")
        self.assertEqual(len(self.store.load_decisions()), before)

        _append_event(
            self.store,
            "confirm_spec",
            spec_id=spec["spec_id"],
            revision=revision,
            sha256=sha256_file(self.store.spec_path),
            reason="forged second confirmation",
        )

        codes = _codes(self.store)
        self.assertIn("E_DECISIONS_CORRUPT", codes)

    def test_confirm_spec_is_idempotent_for_an_untampered_spec(self) -> None:
        revision = self._confirmed()
        before = self.store.load_decisions()
        returned = self.store.confirm_spec(revision)
        after = self.store.load_decisions()

        self.assertEqual(returned["state"], "specified")
        self.assertEqual(len(after), len(before))
        self.assertEqual([item["action"] for item in after].count("confirm_spec"), 1)

    def test_approved_plan_cannot_be_replaced_by_a_raw_plan_proposed(self) -> None:
        _, revision = self._approved()
        replacement = plan_payload(revision, plan_id="plan_0002")
        replacement["steps"][0]["target"] = "assets/evil.png"
        _rewrite(self.store.plan_path, replacement)
        _append_event(
            self.store,
            "plan_proposed",
            plan_id=replacement["plan_id"],
            spec_revision=revision,
            sha256=_plan_content_sha(replacement),
        )
        state = self.store.load_state()
        state["active_plan_id"] = replacement["plan_id"]
        state["approved_plan_id"] = None
        _rewrite(self.store.state_path, state)

        codes = _codes(self.store)
        self.assertIn("E_DECISIONS_CORRUPT", codes)
        self.assertIn("E_STEP_TARGET_NOT_DELIVERABLE", codes)

    def test_proposed_plan_is_checked_for_deliverables_and_acceptance(self) -> None:
        write_file(self.root, "assets/wallpaper/ink.png", b"ink")
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        plan = self.store.save_plan(plan_payload(revision))
        plan["steps"][0]["acceptance"] = ["missing-acceptance"]
        _rewrite(self.store.plan_path, plan)

        codes = _codes(self.store)
        self.assertIn("E_STEP_ACCEPTANCE_REF", codes)

    def test_proposed_plan_budget_over_spec_is_detected(self) -> None:
        write_file(self.root, "assets/wallpaper/ink.png", b"ink")
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        plan = self.store.save_plan(plan_payload(revision))
        plan["steps"][0]["budget"]["max_images"] = 99
        plan["budget"]["max_usd"] = 100
        _rewrite(self.store.plan_path, plan)

        codes = _codes(self.store)
        self.assertIn("E_STEP_BUDGET_EXCEEDS_SPEC", codes)
        self.assertIn("E_PLAN_BUDGET_EXCEEDS_SPEC", codes)

    def test_proposed_plan_with_bad_generator_is_rejected(self) -> None:
        write_file(self.root, "assets/wallpaper/ink.png", b"ink")
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        plan = self.store.save_plan(plan_payload(revision))
        plan["steps"][0]["generator"] = ""
        _rewrite(self.store.plan_path, plan)
        self.assertIn("E_PLAN_INVALID", _codes(self.store))

    def test_proposed_plan_with_unknown_dependency_is_rejected(self) -> None:
        write_file(self.root, "assets/wallpaper/ink.png", b"ink")
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        plan = self.store.save_plan(plan_payload(revision))
        plan["steps"][0]["depends_on"] = ["missing-step"]
        _rewrite(self.store.plan_path, plan)
        self.assertIn("E_PLAN_INVALID", _codes(self.store))

    def test_legal_spec_revision_allows_a_new_plan(self) -> None:
        self._approved()
        add_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
            {"id": "avatar", "path": "assets/avatar.png", "kind": "image"},
        )
        revision = int(self.store.load_spec()["revision"])
        self.store.confirm_spec(revision)
        plan = self.store.save_plan(plan_payload(revision, plan_id="plan_0002"))
        self.store.approve_plan(plan["plan_id"])
        errors = [issue for issue in verify_project(self.store) if issue.severity == "error"]
        self.assertEqual(errors, [])


class PathIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "project"
        self.store = make_store(self.root)

    def test_ads_paths_are_rejected(self) -> None:
        with self.assertRaises(CreationError) as caught:
            self.store.lock("assets/avatar.png:secret")
        self.assertEqual(caught.exception.code, "E_PATH_ADS")

    def test_case_variant_creation_path_is_rejected(self) -> None:
        with self.assertRaises(CreationError) as caught:
            self.store.lock("Creation/state.json")
        self.assertEqual(caught.exception.code, "E_CREATION_PATH")

    @unittest.skipUnless(os.name == "nt", "case-insensitive filesystem comparison is Windows-only")
    def test_lock_identity_is_case_insensitive(self) -> None:
        write_file(self.root, "Assets/Avatar.PNG", b"pixels")
        self.store.lock("assets/avatar.png")
        self.store.lock("ASSETS/AVATAR.PNG")
        self.assertEqual(len(self.store.load_locks()), 1)

    @unittest.skipUnless(os.name == "nt", "case-insensitive filesystem comparison is Windows-only")
    def test_deliverable_matching_is_case_insensitive_and_hashes_the_real_file(self) -> None:
        write_file(self.root, "assets/avatar.png", b"pixels")
        add_deliverables(
            self.store,
            {"id": "avatar", "path": "Assets/Avatar.PNG", "kind": "image"},
        )
        digest = build_digest(self.store)
        item = digest["deliverables"][0]
        self.assertEqual(item["status"], "present")
        self.assertEqual(len(item["sha256"]), 64)
        self.assertEqual(item["size_bytes"], len(b"pixels"))


class JunctionIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.root = self.base / "project"
        self.outside = self.base / "outside"
        self.outside.mkdir(parents=True)

    def test_init_refuses_a_dangling_root_junction(self) -> None:
        if os.name != "nt":
            self.skipTest("junction creation is Windows-only")
        outside = self.base / "outside-not-created"
        link = self.base / "dangling-project"
        if not _make_junction(link, outside):
            self.skipTest("junction creation is not available")

        with self.assertRaises(CreationError) as caught:
            ProjectStore(link)
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertFalse(outside.exists())

    def test_init_rechecks_root_before_writing(self) -> None:
        if os.name != "nt":
            self.skipTest("junction creation is Windows-only")
        root = self.base / "replaced-project"
        store = ProjectStore(root)  # root does not exist yet; valid at construction
        outside = self.base / "outside-init-target"
        if not _make_junction(root, outside):
            self.skipTest("junction creation is not available")

        with self.assertRaises(CreationError) as caught:
            store.init(name="replaced root", pack_type="desktop_theme")
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertFalse(outside.exists())

    def test_ancestor_junction_introduced_after_construction_is_rejected(self) -> None:
        if os.name != "nt":
            self.skipTest("junction creation is Windows-only")
        late_parent = self.base / "late-ancestor"
        store = ProjectStore(late_parent / "project")  # parent does not exist yet
        if not _make_junction(late_parent, self.outside):
            self.skipTest("junction creation is not available")

        with self.assertRaises(CreationError) as caught:
            store.init(name="late ancestor", pack_type="desktop_theme")
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertFalse((self.outside / "project").exists())

    def test_root_below_an_ancestor_junction_is_rejected(self) -> None:
        if os.name != "nt":
            self.skipTest("junction creation is Windows-only")
        (self.outside / "project").mkdir()
        link = self.base / "linked-parent"
        if not _make_junction(link, self.outside):
            self.skipTest("junction creation is not available")

        with self.assertRaises(CreationError) as caught:
            ProjectStore(link / "project")
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")

    def test_lexical_parent_component_cannot_hide_a_junction(self) -> None:
        if os.name != "nt":
            self.skipTest("junction creation is Windows-only")
        link = self.base / "lexical-link"
        if not _make_junction(link, self.outside):
            self.skipTest("junction creation is not available")
        bypass = self.base / "missing" / ".." / link.name / "project"

        with self.assertRaises(CreationError) as caught:
            ProjectStore(bypass)
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertFalse((self.outside / "project").exists())

    def test_multiple_missing_components_do_not_hide_a_junction(self) -> None:
        if os.name != "nt":
            self.skipTest("junction creation is Windows-only")
        link = self.base / "lexical-link"
        if not _make_junction(link, self.outside):
            self.skipTest("junction creation is not available")
        bypass = self.base / "missing-a" / "missing-b" / ".." / ".." / link.name / "project"

        with self.assertRaises(CreationError) as caught:
            ProjectStore(bypass)
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertFalse((self.outside / "project").exists())

    def test_cli_init_rejects_a_lexical_parent_bypass(self) -> None:
        if os.name != "nt":
            self.skipTest("junction creation is Windows-only")
        link = self.base / "cli-lexical-link"
        if not _make_junction(link, self.outside):
            self.skipTest("junction creation is not available")
        bypass = self.base / "missing" / ".." / link.name / "project"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "veyra_creation",
                "init",
                str(bypass),
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
        self.assertIn("E_LINK_BOUNDARY", completed.stderr)
        self.assertFalse((self.outside / "project" / "creation" / "state.json").exists())

    def test_normal_directory_still_initializes(self) -> None:
        store = make_store(self.base / "normal-project")
        self.assertTrue(store.state_path.is_file())
        self.assertEqual(store.load_state()["spec_revision"], 1)

    def test_init_refuses_a_creation_junction(self) -> None:
        self.root.mkdir()
        if not _make_junction(self.root / "creation", self.outside):
            self.skipTest("junction creation is not available")
        store = ProjectStore(self.root)
        with self.assertRaises(CreationError) as caught:
            store.init(name="junction project", pack_type="desktop_theme")
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")
        self.assertFalse((self.outside / "state.json").exists())

    def test_digest_does_not_follow_a_project_junction(self) -> None:
        self.root.mkdir()
        (self.outside / "secret.txt").write_text("outside", encoding="utf-8")
        if not _make_junction(self.root / "junc", self.outside):
            self.skipTest("junction creation is not available")
        store = make_store(self.root)
        paths = {item["path"] for item in build_digest(store)["files"]}
        self.assertNotIn("junc/secret.txt", paths)

    def test_spec_file_replaced_by_a_junction_is_rejected(self) -> None:
        store = make_store(self.root)
        store.spec_path.unlink()
        if not _make_junction(store.spec_path, self.outside):
            self.skipTest("junction creation is not available")
        with self.assertRaises(CreationError) as caught:
            store.load_spec()
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")

    def test_plan_file_replaced_by_a_junction_is_rejected(self) -> None:
        store = make_store(self.root)
        if not _make_junction(store.plan_path, self.outside):
            self.skipTest("junction creation is not available")
        with self.assertRaises(CreationError) as caught:
            store.load_plan()
        self.assertEqual(caught.exception.code, "E_LINK_BOUNDARY")


class DecisionsLogIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = make_store(Path(self._temp.name) / "project")

    def test_missing_trailing_newline_is_rejected_without_appending(self) -> None:
        original = self.store.decisions_path.read_text(encoding="utf-8")
        self.assertTrue(original.endswith("\n"))
        damaged = original[:-1]
        self.store.decisions_path.write_text(damaged, encoding="utf-8")

        with self.assertRaises(CreationError) as caught:
            self.store.load_decisions()
        self.assertEqual(caught.exception.code, "E_DECISIONS_CORRUPT")
        with self.assertRaises(CreationError) as caught:
            self.store.append_decision("note", text="must not concatenate")
        self.assertEqual(caught.exception.code, "E_DECISIONS_CORRUPT")
        self.assertEqual(self.store.decisions_path.read_text(encoding="utf-8"), damaged)
        self.assertIn("E_DECISIONS_CORRUPT", _codes(self.store))

    def test_blank_only_log_is_rejected_without_appending(self) -> None:
        damaged = "\n"
        self.store.decisions_path.write_text(damaged, encoding="utf-8")
        version_before = self.store.load_state()["project_version"]

        with self.assertRaises(CreationError) as caught:
            self.store.load_decisions()
        self.assertEqual(caught.exception.code, "E_DECISIONS_CORRUPT")
        with self.assertRaises(CreationError) as caught:
            self.store.append_decision("note", text="must not append after a blank line")
        self.assertEqual(caught.exception.code, "E_DECISIONS_CORRUPT")
        self.assertEqual(self.store.decisions_path.read_text(encoding="utf-8"), damaged)
        self.assertEqual(self.store.load_state()["project_version"], version_before)

    def test_trailing_blank_line_is_rejected_without_appending(self) -> None:
        original = self.store.decisions_path.read_text(encoding="utf-8")
        damaged = original + "\n"
        self.store.decisions_path.write_text(damaged, encoding="utf-8")
        version_before = self.store.load_state()["project_version"]

        with self.assertRaises(CreationError) as caught:
            self.store.load_decisions()
        self.assertEqual(caught.exception.code, "E_DECISIONS_CORRUPT")
        with self.assertRaises(CreationError) as caught:
            self.store.append_decision("note", text="must not append after a blank line")
        self.assertEqual(caught.exception.code, "E_DECISIONS_CORRUPT")
        self.assertEqual(self.store.decisions_path.read_text(encoding="utf-8"), damaged)
        self.assertEqual(self.store.load_state()["project_version"], version_before)

    def test_empty_log_allows_a_first_append(self) -> None:
        self.store.decisions_path.write_text("", encoding="utf-8")
        event = self.store.append_decision("note", text="first")
        lines = self.store.decisions_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["event_id"], event["event_id"])
        self.assertEqual([item["event_id"] for item in self.store.load_decisions()], [event["event_id"]])

    def test_consecutive_appends_are_independent_json_lines(self) -> None:
        first = self.store.append_decision("note", text="one")
        second = self.store.append_decision("note", text="two")
        lines = self.store.decisions_path.read_text(encoding="utf-8").splitlines()
        parsed = [json.loads(line) for line in lines]
        self.assertEqual([item["event_id"] for item in parsed[-2:]], [first["event_id"], second["event_id"]])
        self.assertEqual([item["event_id"] for item in self.store.load_decisions()][-2:], [first["event_id"], second["event_id"]])


class RunAndPlaceholderIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "project"
        self.store = make_store(self.root)

    def test_run_with_inconsistent_fields_is_detected(self) -> None:
        run = self.store.start_run("generate wallpaper")
        run["status"] = "ok"  # finished without finished_at
        _rewrite(self.store.runs_dir / f"{run['run_id']}.json", run)
        self.assertIn("E_RUN_CORRUPT", _codes(self.store))

    def test_deleted_run_referenced_by_state_is_detected(self) -> None:
        run = self.store.start_run("generate wallpaper")
        (self.store.runs_dir / f"{run['run_id']}.json").unlink()
        self.assertIn("E_RUN_MISSING", _codes(self.store))

    def test_corrupt_placeholders_are_detected(self) -> None:
        self.store.placeholders_path.write_text("{broken", encoding="utf-8")
        self.assertIn("E_PLACEHOLDERS_CORRUPT", _codes(self.store))


if __name__ == "__main__":
    unittest.main()

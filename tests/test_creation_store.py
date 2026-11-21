"""Tests for creation-project state, Spec revisions, locks and runs."""

from __future__ import annotations

import json
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

from veyra_creation.digest import verify_project
from veyra_creation.errors import CreationError
from veyra_creation.store import ProjectStore


class StoreInitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def test_init_creates_versioned_state_and_files(self) -> None:
        store = make_store(self.root / "project")
        self.assertTrue(store.state_path.is_file())
        self.assertTrue(store.spec_path.is_file())
        self.assertTrue(store.decisions_path.is_file())
        self.assertTrue(store.locks_path.is_file())
        self.assertTrue(store.placeholders_path.is_file())
        self.assertTrue(store.runs_dir.is_dir())
        state = store.load_state()
        self.assertEqual(state["schema_version"], "1.0.0")
        self.assertEqual(state["tool"]["name"], "veyra-creation")
        self.assertEqual(state["project_version"], 1)
        self.assertEqual(state["spec_revision"], 1)
        self.assertEqual(state["spec_state"], "draft")
        self.assertIsNone(state["confirmed_spec_revision"])
        spec = store.load_spec()
        self.assertEqual(spec["revision"], 1)
        self.assertEqual(spec["state"], "draft")
        self.assertEqual(spec["deliverables"], [])
        events = store.load_decisions()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "project_created")
        self.assertEqual(store.load_locks(), [])

    def test_init_refuses_an_existing_state(self) -> None:
        store = make_store(self.root / "project")
        with self.assertRaises(CreationError) as caught:
            store.init(name="Again", pack_type="mod")
        self.assertEqual(caught.exception.code, "E_PROJECT_EXISTS")

    def test_init_refuses_a_non_empty_creation_directory(self) -> None:
        root = self.root / "project"
        (root / "creation").mkdir(parents=True)
        (root / "creation" / "leftover.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(CreationError) as caught:
            ProjectStore(root).init(name="Demo", pack_type="mod")
        self.assertEqual(caught.exception.code, "E_PROJECT_EXISTS")

    def test_init_rejects_an_invalid_name_without_writing(self) -> None:
        root = self.root / "project"
        with self.assertRaises(CreationError) as caught:
            ProjectStore(root).init(name="", pack_type="mod")
        self.assertEqual(caught.exception.code, "E_SPEC_INVALID")
        self.assertFalse((root / "creation" / "state.json").exists())

    def test_init_rejects_an_invalid_pack_type(self) -> None:
        with self.assertRaises(CreationError) as caught:
            ProjectStore(self.root / "project").init(name="Demo", pack_type="rocket")
        self.assertEqual(caught.exception.code, "E_SPEC_INVALID")


class SpecRevisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = make_store(Path(self._temp.name) / "project")

    def test_save_spec_requires_the_next_revision(self) -> None:
        spec = self.store.load_spec()
        spec["revision"] = 3
        with self.assertRaises(CreationError) as caught:
            self.store.save_spec(spec)
        self.assertEqual(caught.exception.code, "E_REVISION_CONFLICT")

    def test_save_spec_advances_revision_and_version(self) -> None:
        spec = add_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper.png", "kind": "image"},
        )
        self.assertEqual(spec["revision"], 2)
        state = self.store.load_state()
        self.assertEqual(state["spec_revision"], 2)
        self.assertEqual(state["spec_state"], "draft")
        self.assertGreater(state["project_version"], 1)

    def test_confirm_spec_freezes_the_current_revision(self) -> None:
        confirmed = self.store.confirm_spec(1, reason="looks right")
        self.assertEqual(confirmed["state"], "specified")
        state = self.store.load_state()
        self.assertEqual(state["spec_state"], "specified")
        self.assertEqual(state["confirmed_spec_revision"], 1)
        events = self.store.load_decisions()
        self.assertEqual(events[-1]["action"], "confirm_spec")
        self.assertEqual(events[-1]["reason"], "looks right")

    def test_a_new_draft_revision_clears_confirmation(self) -> None:
        self.store.confirm_spec(1)
        spec = add_deliverables(
            self.store,
            {"id": "avatar", "path": "assets/avatar.png", "kind": "image"},
        )
        self.assertEqual(spec["revision"], 2)
        state = self.store.load_state()
        self.assertIsNone(state["confirmed_spec_revision"])
        self.assertEqual(state["spec_state"], "draft")

    def test_confirm_rejects_a_stale_revision(self) -> None:
        self.store.confirm_spec(1)
        with self.assertRaises(CreationError) as caught:
            self.store.confirm_spec(99)
        self.assertEqual(caught.exception.code, "E_REVISION_CONFLICT")


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = make_store(Path(self._temp.name) / "project")
        _, self.revision = confirmed_with_deliverables(
            self.store,
            {
                "id": "wallpaper",
                "path": "assets/wallpaper/ink.png",
                "kind": "image",
            },
        )

    def test_plan_must_target_the_current_spec_revision(self) -> None:
        with self.assertRaises(CreationError) as caught:
            self.store.save_plan(plan_payload(99))
        self.assertEqual(caught.exception.code, "E_PLAN_INVALID")

    def test_plan_approval_is_recorded_and_blocks_replacement(self) -> None:
        plan = self.store.save_plan(plan_payload(self.revision))
        self.assertEqual(plan["state"], "proposed")
        approved = self.store.approve_plan(plan["plan_id"])
        self.assertEqual(approved["state"], "approved")
        state = self.store.load_state()
        self.assertEqual(state["approved_plan_id"], plan["plan_id"])
        self.assertEqual(state["active_plan_id"], plan["plan_id"])
        events = self.store.load_decisions()
        self.assertEqual(events[-1]["action"], "approve_plan")
        with self.assertRaises(CreationError) as caught:
            self.store.save_plan(plan_payload(self.revision, plan_id="plan_0002"))
        self.assertEqual(caught.exception.code, "E_PLAN_APPROVED")
        self.assertEqual([issue for issue in verify_project(self.store) if issue.severity == "error"], [])

    def test_plan_rejects_a_draft_spec(self) -> None:
        other = make_store(Path(self._temp.name) / "draft")
        with self.assertRaises(CreationError) as caught:
            other.save_plan(plan_payload(1))
        self.assertEqual(caught.exception.code, "E_SPEC_NOT_CONFIRMED")

    def test_plan_rejects_a_target_outside_the_spec(self) -> None:
        bad = plan_payload(
            self.revision,
            steps=[
                {
                    "id": "paint-other",
                    "action": "generate_image",
                    "target": "assets/other.png",
                    "generator": "image_default",
                    "budget": {"max_usd": 0.1},
                }
            ],
        )
        with self.assertRaises(CreationError) as caught:
            self.store.save_plan(bad)
        self.assertEqual(caught.exception.code, "E_PLAN_INVALID")
        self.assertFalse(self.store.plan_path.exists())

    def test_revising_spec_supersedes_the_approved_plan(self) -> None:
        plan = self.store.save_plan(plan_payload(self.revision))
        self.store.approve_plan(plan["plan_id"])
        add_deliverables(
            self.store,
            {"id": "avatar", "path": "assets/avatar.png", "kind": "image"},
        )
        reloaded = self.store.load_plan()
        self.assertEqual(reloaded["state"], "superseded")
        state = self.store.load_state()
        self.assertIsNone(state["active_plan_id"])
        self.assertIsNone(state["approved_plan_id"])
        errors = [issue for issue in verify_project(self.store) if issue.severity == "error"]
        self.assertEqual(errors, [])

    def test_invalid_plan_is_rejected_before_writing(self) -> None:
        bad = plan_payload(
            self.revision,
            steps=[{"id": "x", "action": "delete", "target": "../escape"}],
        )
        with self.assertRaises(CreationError) as caught:
            self.store.save_plan(bad)
        self.assertEqual(caught.exception.code, "E_PLAN_INVALID")
        self.assertFalse(self.store.plan_path.exists())


class PlanApprovalIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = make_store(Path(self._temp.name) / "project")
        _, self.revision = confirmed_with_deliverables(
            self.store,
            {
                "id": "wallpaper",
                "path": "assets/wallpaper/ink.png",
                "kind": "image",
            },
        )

    def _approved_plan(self) -> dict:
        plan = self.store.save_plan(plan_payload(self.revision))
        return self.store.approve_plan(plan["plan_id"])

    def _write_decisions(self, events: list[dict]) -> None:
        payload = "".join(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            for event in events
        )
        self.store.decisions_path.write_text(payload, encoding="utf-8")

    def test_repeated_approval_is_idempotent_without_new_event(self) -> None:
        approved = self._approved_plan()
        events = self.store.load_decisions()

        again = self.store.approve_plan(approved["plan_id"])

        self.assertEqual(again["state"], "approved")
        self.assertEqual(self.store.load_decisions(), events)
        errors = [issue for issue in verify_project(self.store) if issue.severity == "error"]
        self.assertEqual(errors, [])

    def test_approval_rejects_plan_tampered_after_approval(self) -> None:
        approved = self._approved_plan()
        plan = json.loads(self.store.plan_path.read_text(encoding="utf-8"))
        plan["steps"][0]["prompt"] = "changed after approval"
        self.store.plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        events = self.store.load_decisions()

        with self.assertRaises(CreationError) as caught:
            self.store.approve_plan(approved["plan_id"])

        self.assertEqual(caught.exception.code, "E_PLAN_TAMPERED")
        self.assertEqual(self.store.load_decisions(), events)

    def test_approval_rejects_missing_approval_event(self) -> None:
        approved = self._approved_plan()
        events = self.store.load_decisions()
        filtered = [event for event in events if event.get("action") != "approve_plan"]
        self.assertEqual(len(filtered), len(events) - 1)
        self._write_decisions(filtered)
        before = self.store.load_decisions()

        with self.assertRaises(CreationError) as caught:
            self.store.approve_plan(approved["plan_id"])

        self.assertEqual(caught.exception.code, "E_PLAN_TAMPERED")
        self.assertEqual(self.store.load_decisions(), before)

    def test_approval_rejects_ambiguous_approval_events(self) -> None:
        approved = self._approved_plan()
        events = self.store.load_decisions()
        duplicate = dict(events[-1])
        duplicate["event_id"] = "dec_duplicate_approve"
        duplicate["prev_event_id"] = events[-1]["event_id"]
        self._write_decisions(events + [duplicate])
        before = self.store.load_decisions()

        with self.assertRaises(CreationError) as caught:
            self.store.approve_plan(approved["plan_id"])

        self.assertEqual(caught.exception.code, "E_PLAN_TAMPERED")
        self.assertEqual(self.store.load_decisions(), before)


class DecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = make_store(Path(self._temp.name) / "project")

    def test_decisions_are_append_only_and_bump_project_version(self) -> None:
        before = self.store.load_state()["project_version"]
        self.store.append_decision("note", text="keep the umbrella")
        events = self.store.load_decisions()
        self.assertEqual(events[-1]["action"], "note")
        self.assertEqual(events[-1]["text"], "keep the umbrella")
        self.assertEqual(self.store.load_state()["project_version"], before + 1)

    def test_corrupt_decision_line_is_detected(self) -> None:
        self.store.decisions_path.write_text("{not json}\n", encoding="utf-8")
        with self.assertRaises(CreationError) as caught:
            self.store.load_decisions()
        self.assertEqual(caught.exception.code, "E_DECISIONS_CORRUPT")

    def test_duplicate_event_id_is_rejected(self) -> None:
        self.store.append_decision("note", event_id="dec_fixed")
        with self.assertRaises(CreationError) as caught:
            self.store.append_decision("note", event_id="dec_fixed")
        self.assertEqual(caught.exception.code, "E_DUPLICATE_EVENT")

    def test_unknown_actor_is_rejected(self) -> None:
        with self.assertRaises(CreationError) as caught:
            self.store.append_decision("note", actor="model")
        self.assertEqual(caught.exception.code, "E_DECISION_ACTOR")


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name) / "project"
        self.store = make_store(self.root)
        write_file(self.root, "assets/avatar.png", b"avatar-v1")

    def test_lock_and_unlock_are_hash_bound(self) -> None:
        item = self.store.lock("assets/avatar.png", reason="approved")
        self.assertTrue(self.store.is_locked("assets/avatar.png"))
        self.assertEqual(item["sha256"], self.store.load_locks()[0]["sha256"])
        self.store.unlock("assets/avatar.png")
        self.assertFalse(self.store.is_locked("assets/avatar.png"))
        actions = [event["action"] for event in self.store.load_decisions()]
        self.assertIn("lock", actions)
        self.assertIn("unlock", actions)

    def test_lock_conflict_after_the_file_changes(self) -> None:
        self.store.lock("assets/avatar.png")
        write_file(self.root, "assets/avatar.png", b"avatar-v2")
        with self.assertRaises(CreationError) as caught:
            self.store.lock("assets/avatar.png")
        self.assertEqual(caught.exception.code, "E_LOCK_CONFLICT")

    def test_locking_a_missing_file_is_rejected(self) -> None:
        with self.assertRaises(CreationError) as caught:
            self.store.lock("assets/missing.png")
        self.assertEqual(caught.exception.code, "E_LOCK_FILE_MISSING")

    def test_locking_an_internal_path_is_rejected(self) -> None:
        with self.assertRaises(CreationError) as caught:
            self.store.lock("creation/spec.json")
        self.assertEqual(caught.exception.code, "E_CREATION_PATH")


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.store = make_store(Path(self._temp.name) / "project")

    def test_runs_start_finish_and_are_listed(self) -> None:
        run = self.store.start_run("generate wallpaper", model="image-model")
        self.assertEqual(run["status"], "running")
        finished = self.store.finish_run(
            run["run_id"],
            status="ok",
            usage={"input_tokens": 10, "output_tokens": 0, "images": 1},
            cost_usd=0.02,
            cost_source="estimated",
            outputs=[{"path": "assets/wallpaper.png"}],
        )
        self.assertEqual(finished["status"], "ok")
        self.assertIsNotNone(finished["finished_at"])
        runs = self.store.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], run["run_id"])
        self.assertEqual(self.store.load_state()["last_run_id"], run["run_id"])

    def test_finishing_a_run_twice_is_rejected(self) -> None:
        run = self.store.start_run("generate wallpaper")
        self.store.finish_run(run["run_id"], status="ok")
        with self.assertRaises(CreationError) as caught:
            self.store.finish_run(run["run_id"], status="failed")
        self.assertEqual(caught.exception.code, "E_RUN_FINISHED")

    def test_list_runs_reports_a_corrupt_record(self) -> None:
        self.store.runs_dir.mkdir(parents=True, exist_ok=True)
        (self.store.runs_dir / "run_bad.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(CreationError) as caught:
            self.store.list_runs()
        self.assertEqual(caught.exception.code, "E_RUN_CORRUPT")
        self.assertIn("run_bad.json", caught.exception.message)

    def test_status_summarises_the_project(self) -> None:
        _, revision = confirmed_with_deliverables(
            self.store,
            {"id": "wallpaper", "path": "assets/wallpaper/ink.png", "kind": "image"},
        )
        self.store.save_plan(plan_payload(revision))
        status = self.store.status()
        self.assertEqual(status["spec"]["revision"], revision)
        self.assertEqual(status["plan"]["plan_id"], "plan_0001")
        self.assertEqual(status["locks"], 0)
        self.assertEqual(status["runs"]["count"], 0)


class IdentifierValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)

    def test_init_accepts_none_and_legal_ids(self) -> None:
        automatic = ProjectStore(self.root / "automatic")
        state = automatic.init(name="Demo", pack_type="mod")
        self.assertRegex(state["project_id"], r"^prj_[a-z0-9][a-z0-9._-]{0,63}$")
        self.assertRegex(automatic.load_spec()["spec_id"], r"^spec_[a-z0-9][a-z0-9._-]{0,63}$")

        explicit = ProjectStore(self.root / "explicit")
        state = explicit.init(
            name="Demo",
            pack_type="mod",
            project_id="prj_custom",
            spec_id="spec_custom",
        )
        self.assertEqual(state["project_id"], "prj_custom")
        self.assertEqual(explicit.load_spec()["spec_id"], "spec_custom")

    def test_init_rejects_non_string_ids_without_writing(self) -> None:
        for field, code, location in (
            ("project_id", "E_PROJECT_ID", "project"),
            ("spec_id", "E_SPEC_ID", "spec"),
        ):
            for index, value in enumerate((0, 1, 1.25, True, [], {})):
                with self.subTest(field=field, value=value):
                    store = ProjectStore(self.root / f"bad_{field}_{index}")
                    with self.assertRaises(CreationError) as caught:
                        store.init(name="Demo", pack_type="mod", **{field: value})
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.location, location)
                    self.assertFalse(store.state_path.exists())
                    self.assertFalse(store.creation_dir.exists())

    def test_init_keeps_empty_and_invalid_string_errors(self) -> None:
        for field, code, location in (
            ("project_id", "E_PROJECT_ID", "project"),
            ("spec_id", "E_SPEC_ID", "spec"),
        ):
            for index, value in enumerate(("", "Bad ID")):
                with self.subTest(field=field, value=value):
                    store = ProjectStore(self.root / f"string_{field}_{index}")
                    with self.assertRaises(CreationError) as caught:
                        store.init(name="Demo", pack_type="mod", **{field: value})
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(caught.exception.location, location)
                    self.assertFalse(store.state_path.exists())

    def test_start_run_accepts_none_and_legal_ids(self) -> None:
        store = make_store(self.root / "runs_ok")
        automatic = store.start_run("task")
        self.assertRegex(automatic["run_id"], r"^run_[a-z0-9][a-z0-9._-]{0,63}$")
        explicit = store.start_run("task", run_id="run_custom")
        self.assertEqual(explicit["run_id"], "run_custom")

    def test_start_run_rejects_non_string_ids_without_writing(self) -> None:
        for index, value in enumerate((0, 1, 1.25, True, [], {})):
            with self.subTest(value=value):
                store = make_store(self.root / f"bad_run_{index}")
                before_state = store.load_state()
                before_events = store.load_decisions()
                with self.assertRaises(CreationError) as caught:
                    store.start_run("task", run_id=value)
                self.assertEqual(caught.exception.code, "E_RUN_ID")
                self.assertEqual(caught.exception.location, "runs")
                self.assertEqual(store.load_state(), before_state)
                self.assertEqual(store.load_decisions(), before_events)
                self.assertEqual(store.list_runs(), [])
                self.assertEqual(list(store.runs_dir.glob("*.json")), [])

    def test_start_run_keeps_empty_and_invalid_string_errors(self) -> None:
        for index, value in enumerate(("", "not-a-run")):
            with self.subTest(value=value):
                store = make_store(self.root / f"string_run_{index}")
                before_state = store.load_state()
                before_events = store.load_decisions()
                with self.assertRaises(CreationError) as caught:
                    store.start_run("task", run_id=value)
                self.assertEqual(caught.exception.code, "E_RUN_ID")
                self.assertEqual(caught.exception.location, "runs")
                self.assertEqual(store.load_state(), before_state)
                self.assertEqual(store.load_decisions(), before_events)
                self.assertEqual(store.list_runs(), [])
                self.assertEqual(list(store.runs_dir.glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()

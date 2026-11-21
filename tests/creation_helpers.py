"""Shared helpers for the ``veyra_creation`` tests.

Every helper works on a temporary directory supplied by the caller.  No helper
reads or writes the repository itself.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from veyra_creation.plan import PLAN_ACTIONS
from veyra_creation.store import ProjectStore

FIXED_CLOCK = "2026-11-21T00:00:00Z"


def run_creation_cli(argv: list[str]) -> tuple[int, str, str]:
    from veyra_creation.cli import main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def make_store(root: Path, *, name: str = "Suoming Theme", **kwargs: Any) -> ProjectStore:
    store = ProjectStore(root, clock=lambda: FIXED_CLOCK)
    store.init(
        name=name,
        pack_type=kwargs.pop("pack_type", "desktop_theme"),
        target_game=kwargs.pop("target_game", "Wuthering Waves"),
        **kwargs,
    )
    return store


def write_file(root: Path, relative: str, data: bytes | str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    return path


def add_deliverables(
    store: ProjectStore,
    *entries: dict[str, Any],
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new draft revision carrying the given deliverables."""

    spec = store.load_spec()
    new_spec = json.loads(json.dumps(spec))
    new_spec["revision"] = spec["revision"] + 1
    new_spec["state"] = "draft"
    new_spec["deliverables"] = list(entries)
    constraints = new_spec.setdefault("constraints", {})
    constraints["budget"] = budget or {
        "max_usd": 10.0,
        "max_images": 20,
        "max_input_tokens": 100000,
        "max_output_tokens": 100000,
    }
    return store.save_spec(new_spec)


def confirmed_with_deliverables(
    store: ProjectStore, *entries: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """Add deliverables to a new revision and confirm it for Plan tests."""

    spec = add_deliverables(store, *entries)
    revision = int(spec["revision"])
    store.confirm_spec(revision)
    return spec, revision


def plan_payload(
    spec_revision: int,
    *,
    plan_id: str = "plan_0001",
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if steps is None:
        steps = [
            {
                "id": "paint-wallpaper",
                "action": "generate_image",
                "target": "assets/wallpaper/ink.png",
                "generator": "image_default",
                "budget": {"max_usd": 0.5, "max_images": 1},
                "prompt": "ink wash",
            }
        ]
    return {
        "schema_version": "1.0.0",
        "plan_id": plan_id,
        "spec_revision": spec_revision,
        "state": "proposed",
        "steps": steps,
        "budget": {"max_usd": 1.0, "max_images": 5},
    }


def step(step_id: str, action: str, target: str | None = None, **extra: Any) -> dict[str, Any]:
    assert action in PLAN_ACTIONS
    value: dict[str, Any] = {"id": step_id, "action": action}
    if target is not None:
        value["target"] = target
    value.update(extra)
    return value

"""Constants shared by the deterministic creation-project core."""

from __future__ import annotations

CREATION_SCHEMA_VERSION = "1.0.0"

#: Version of the ``veyra-creation`` command line tool.
CREATION_TOOL_VERSION = "0.1.0"

CREATION_TOOL_NAME = "veyra-creation"

#: Only a schema with this major version can be consumed by this tool.
SUPPORTED_SCHEMA_MAJOR = 1

#: Internal state lives here and is never exported into a Pack.
CREATION_DIRNAME = "creation"

STATE_FILENAME = "state.json"
SPEC_FILENAME = "spec.json"
PLAN_FILENAME = "plan.json"
DECISIONS_FILENAME = "decisions.ndjson"
LOCKS_FILENAME = "locks.json"
PLACEHOLDERS_FILENAME = "placeholders.json"
DIGEST_FILENAME = "digest.json"
RUNS_DIRNAME = "runs"

SPEC_STATES = ("draft", "specified")
PLAN_STATES = ("proposed", "approved", "superseded")

#: Prefixes every user-facing decision event.
DECISION_PREFIX = "dec_"

#: Directories never included in a project digest or an exported pack.
DIGEST_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
    }
)

#: The project's own state directory is excluded only at the project root.
DIGEST_EXCLUDED_ROOT_DIRS = frozenset({CREATION_DIRNAME})

#: Exact file names the digest must never read (user-protected repository files).
DIGEST_EXCLUDED_FILES = frozenset({"real_crash.log"})

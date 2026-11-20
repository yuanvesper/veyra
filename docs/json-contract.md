# Veyra JSON Contract (`schema_version` 1.0.0)

Status: **implemented** (Phase 2A). This document describes the machine-readable output of
`python triage_agent.py <log> --format json`. It is the interface used by the Agent Skill
(`skills/mod-issue-triage/`, Phase 2B) and by any other downstream tooling.

- Contract version: `1.0.0`
- Introduced in: Veyra 1.2.0
- JSON Schema: [`docs/report.schema.json`](./report.schema.json)
- Markdown reports are **unchanged**; JSON is an additional serialization of the same
  validated analysis object.

## How to obtain the JSON

```bash
# stdout: complete JSON, nothing else. Progress/errors go to stderr.
TRIAGE_LOCAL_MODE=1 python triage_agent.py demo/sample-crash.log --format json

# file: atomic replace, same safety rules as report.md
python triage_agent.py demo/sample-crash.log --format json -o result.json
```

Rules:

- `--format json` without `-o` writes JSON to **stdout** and creates no file.
- `--format json -o <path>` writes JSON to `<path>`; stdout receives only progress text.
  Use `--output=<path>` if you prefer.
- Progress lines, warnings about suspicious input, and errors are written to **stderr** in
  JSON mode, so `stdout` can be piped directly into a JSON parser.
- A write failure exits non-zero, keeps the previous file, and never prints a success
  message.
- If validation fails fatally (for example, the model output is unusable in API mode), no
  JSON object is printed: the error goes to stderr and the exit code is non-zero.
- Writing over the input log is refused (exit code 2).
- For compatibility, `--format markdown` and omitting `--format` behave exactly as before.

## Guarantees

1. **Evidence is Python-owned.** `evidence[].id`, `start_line`, `end_line` and `content`
   come from `extract_evidence()`. Serialization copies them verbatim: IDs are never
   regenerated, renumbered, or invented.
2. **Unknown environment values are `null`.** There are no `"unknown"`, `"未提供"`, or
   empty-string placeholders in `environment`.
3. **Conflicts are preserved.** If one field has multiple distinct candidates, the field is
   `null` and the candidates appear in `conflicts`; nothing is silently chosen.
4. **Python facts win.** `environment` always reflects deterministic extraction. In API
   mode, environment values claimed by the model are kept separately in
   `model_environment` and never overwrite `environment`.
5. **No fabricated warning text.** `warnings` contains validation findings only — not
   progress logs or debug output.

## Top-level fields

| Field | Type | Notes |
| --- | --- | --- |
| `schema_version` | string | `"1.0.0"`. Consumers should key compatibility off this value. |
| `tool` | object | `{"name": "Veyra", "version": "<CLI version>"}`; `version` equals `--version` minus the tool name. |
| `mode` | string | `"local"` (no model call) or `"api"` (OpenAI-compatible call). |
| `summary` | object | Narrative fields, see below. |
| `environment` | object | Python-extracted facts; 5 keys, each `string` or `null`. |
| `model_environment` | object \| null | `null` in local mode; in API mode the model's own claims, never authoritative. |
| `mods` | array | `{"name", "version"}` objects from explicit log lines. |
| `evidence` | array | Extracted evidence blocks with real line numbers. |
| `missing_information` | array of strings | What the analysis still needs, in the report's wording. |
| `conflicts` | object | Field name → list of candidate values. |
| `warnings` | array of strings | Validation warnings. |

## `summary`

| Field | Type | Notes |
| --- | --- | --- |
| `issue_title` | string | Non-empty. |
| `symptoms` | string | Observed symptoms only. |
| `reproduction_steps` | array of strings | May be empty. |
| `current_assessment` | string | Must not assert an unproven root cause. |
| `evidence_refs` | array of strings | Must be a subset of `evidence[].id`. |
| `disclaimer` | string | Always present. |

In local mode these fields are rule-generated. In API mode they come from the model after
validation; invalid evidence references are dropped and reported in `warnings`.

## `environment`

Exactly five keys, each a `string` or `null`:

| Key | Source in the log |
| --- | --- |
| `game_version` | `Minecraft Version:` or `Loading Minecraft <version> with ...` |
| `java_version` | `Java Version:` |
| `os` | `Operating System:` |
| `loader` | loader name in `Loading Minecraft ... with <loader> ...` |
| `loader_version` | loader version in the same line |

`mod_version` is intentionally **not** part of the contract: it is a composite
`"<name> <version>"` summary for Markdown. Structured mod data lives in `mods`.

The same extraction runs in local and API mode, so identical logs produce identical
`environment`, `mods`, and `conflicts` regardless of mode.

## `model_environment`

Only meaningful when `mode == "api"`. Same five keys as `environment`; each value is the
model's claim, or `null`. These values are **not** verified against the log and must not be
treated as facts. Empty strings and placeholders are normalized to `null`.

## `mods`

Derived from `Found mod file <name>-<version>.jar` lines and the crash report's `Mod List`
table. Each entry:

```json
{ "name": "demo_mod", "version": "1.0.0" }
```

Names and versions are deduplicated by `(name, version)`. No version is inferred when the
log does not state it.

## `evidence`

Each entry:

| Key | Type | Meaning |
| --- | --- | --- |
| `id` | string | `E1`, `E2`, … assigned by Python. |
| `type` | string | `exception`, `caused_by`, `suppressed`, or `suspicious_text`. |
| `start_line` | integer ≥ 1 | First real log line of the block (1-based). |
| `end_line` | integer ≥ 1 | Last real log line of the block, inclusive. |
| `content` | string | Exact extracted text, lines joined with `\n`. |
| `truncated` | boolean | True when the block hit a stack-line or size limit; `content` then ends with `[已截断]`. |

`content.split("\n")[0]` is the log's `start_line`. For non-truncated blocks the last
element equals `end_line`; truncated blocks append a `[已截断]` marker line and set
`truncated: true`. Consumers must not renumber, merge, or regenerate these entries.

## `missing_information` and `conflicts`

- Fields already extracted are **not** repeated in `missing_information`.
- A conflicted field is listed in `missing_information` with its candidates and is `null`
  in `environment`.
- `conflicts` is an object mapping a field name to the raw candidate strings, e.g.
  `{"game_version": ["1.20.4", "1.21.1"]}`.

## `warnings`

Validation findings, for example an evidence reference that did not exist, a suspicious
environment value, or deterministic root-cause wording. Values are for human review; they
are not errors and do not change exit codes.

## Versioning policy

- `schema_version` follows the contract, not the tool release.
- Compatible additions (new optional fields, new enum-like strings) bump the minor or patch
  part and update the schema; the schema describes exactly what the producer emits for a
  given version, while consumers should tolerate and ignore unknown fields.
- Removing a field, renaming it, changing its type, or changing its meaning requires a new
  major version (for example `2.0.0`) and an updated schema.
- Consumers should reject or warn on an unknown major version instead of guessing.

## Consumption rules for the Skill (Phase 2B)

The Skill integration is **implemented** as `skills/mod-issue-triage/`. It must:

1. Read only the JSON contract — it does not re-parse the raw log to recreate evidence.
2. Cite only `evidence[].id` values present in the JSON (typically those listed in
   `summary.evidence_refs`).
3. Never invent line numbers: show `start_line`–`end_line` exactly as provided.
4. Use `missing_information` to ask the player for what is genuinely absent; do not ask for
   facts already present in `environment` or `mods`.
5. Respect `conflicts` instead of picking a version.
6. Keep `model_environment` separate from `environment` when explaining the setup.
7. Treat `warnings` as review hints, not as conclusions.

The Skill must stop on an unsupported `schema_version` major version, a missing required
field, or malformed evidence; it must not silently repair the JSON. It can be checked with
`skills/mod-issue-triage/scripts/validate_issue_json.py <file>` (exit code 0 valid, 2 invalid,
1 unreadable JSON), whose rules are kept consistent with `docs/report.schema.json` by
`tests/test_skill_validation.py`.

Still not provided by this contract: per-field confidence scores, root-cause labels, and a
player-facing reply draft. The reply draft is produced by the Skill from the contract data;
the contract itself does not contain it.

## Validation

The contract is covered by `tests/test_json_output.py`, which checks parseability, required
fields and types, `null` serialization, local/API environment consistency, conflict
preservation, evidence ID/line integrity, truncation flags, file-write safety, stdout
purity, and schema-file/field agreement. `tests/test_skill_validation.py` checks the Skill
side of the contract: the validator's rules, the 10 eval-case reports, validator/schema
agreement, and the required statements in the Skill documents. Run:

```bash
python -m unittest discover -s tests
```

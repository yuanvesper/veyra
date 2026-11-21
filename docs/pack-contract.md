# Veyra Pack Contract (`schema_version` 1.0.0)

Status: **implemented** (Phase 3.1, `veyra_pack` 0.1.0).

This document defines the on-disk format of a Veyra creation pack: a directory whose root
contains `pack.json` plus every file the manifest declares. It is deliberately independent
from the crash-triage JSON contract (`docs/json-contract.md`): a triage report describes one
crash-log analysis, a pack describes a piece of creative work (a character kit, a desktop
theme, a mod project, ...). The two schemas are versioned separately and share no fields.

The reference implementation lives in `veyra_pack/` and is reachable as:

```bash
python -m veyra_pack init <dir> --name "My Kit" --type character_kit
python -m veyra_pack validate <dir> [--json]
python -m veyra_pack export <dir> [-o out.zip] [--overwrite]
```

It is offline and stdlib-only: no model calls, no network access, no third-party packages.
`triage_agent.py` is not imported and is unaffected by this contract.

## Layout

```text
my-kit/
  pack.json          # required manifest
  assets/            # convention only; any relative directory layout works
  files/
```

`pack.json` is UTF-8 (an optional BOM is accepted). Every other path in the manifest is a
POSIX-style path relative to the pack root. `pack.json` must not list itself in `files` or
`assets`; the exporter always adds it as the first archive member.

## Top-level fields

| Field | Type | Rules |
| --- | --- | --- |
| `schema_version` | string | `MAJOR.MINOR.PATCH` (optional pre-release/build suffix). `MAJOR` must be `1`, `MINOR` must be `<= 0`; the patch number is informational. |
| `tool` | object | Producer identity: `name` and `version`, both non-empty strings. |
| `pack_id` | string | Stable identifier, `^[a-z0-9][a-z0-9._-]{0,63}$`. |
| `name` | string | Human readable name, 1–200 characters. |
| `description` | string | Up to 20 000 characters; may be empty. |
| `pack_type` | string | One of `character_kit`, `desktop_theme`, `companion`, `asset_pack`, `mod`, `plugin`, `other`. |
| `target_game` | string or null | Up to 200 characters. `null` means game-agnostic. |
| `version` | string | `MAJOR.MINOR.PATCH` with optional suffix (same grammar as `schema_version`). |
| `author` | string or null | Up to 200 characters. |
| `assets` | array | Asset descriptors, see below. |
| `files` | array | File descriptors, see below. |
| `metadata` | object | Free-form object produced by the tool. `init` writes `{"generated_by": "veyra-pack 0.1.0"}`. |

All twelve keys are required. Unknown top-level keys, and unknown keys inside `tool`,
`assets[]`, `files[]`, `license`, are reported as `W_UNKNOWN_FIELD` warnings and are never
rewritten or dropped: the validator is read-only and the exporter stores `pack.json`
byte-for-byte.

### `files[]`

| Field | Type | Rules |
| --- | --- | --- |
| `path` | string | Required, safe relative path (see Path safety). |
| `sha256` | string | Optional; 64 lowercase/uppercase hex characters. Checked against the file. |
| `size_bytes` | integer | Optional; checked against the file size. |
| `mime` / `description` | string | Optional documentation fields. |

At least one of `sha256` / `size_bytes` is recommended; a missing `sha256` produces
`W_HASH_MISSING`.

### `assets[]`

| Field | Type | Rules |
| --- | --- | --- |
| `id` | string | Required, same grammar as `pack_id`, unique (case-insensitive). |
| `path` | string | Required, safe relative path; must also appear in `files[]` or be validated on its own. |
| `kind` | string | Required; one of `image`, `audio`, `video`, `model`, `texture`, `document`, `data`, `other`. |
| `sha256` / `size_bytes` | optional | Same checks as `files[]`. |
| `width` / `height` | optional integers | Must be declared together and are verified against the image header when the file is a PNG, JPEG, GIF or WebP image. |
| `license` | object or null | `{spdx, source, note}`, each an optional string. Missing license → `W_LICENSE_MISSING` warning. |
| `description` | string | Optional. |

## Path safety

Before the filesystem is touched, every declared path is rejected when it:

- is empty, contains NUL, or uses `\` instead of `/`;
- is absolute (`/...`) or a Windows drive path (`C:...`);
- contains `.` or `..` segments (so no path can escape the pack root);
- has a segment ending in a dot or space, or a reserved Windows device name (`CON`, `NUL`,
  `COM1`–`COM9`, `LPT1`–`LPT9`, ...);
- exceeds 512 characters or 32 segments.

After joining, each existing component is inspected with `lstat`: symlinks and NTFS
junctions are rejected (`E_SYMLINK`), and the resolved target must still be inside the pack
root (`E_PATH_ESCAPE`). Only regular files are accepted; directories and other file types
produce `E_NOT_A_FILE`.

## Integrity and limits

When declared, `sha256` and `size_bytes` must match the actual bytes
(`E_HASH_MISMATCH`, `E_SIZE_MISMATCH`, `E_FIELD_FORMAT`). Image dimensions are read from
the header only (no pixel decoding) for PNG, JPEG, GIF and WebP, and must match declared
`width`/`height` (`E_IMAGE_DIMENSION_MISMATCH`, `E_IMAGE_UNREADABLE`,
`E_IMAGE_DIMENSION_PARTIAL`). Unrecognised image headers produce
`W_IMAGE_DIMENSION_UNKNOWN` instead of a hard failure.

Default hard limits (`veyra_pack.contract.Limits`, all configurable by library callers):

| Limit | Default |
| --- | --- |
| `pack.json` size | 1 MiB |
| per-file size | 64 MiB |
| total declared bytes | 256 MiB |
| number of files | 512 |
| image side | 16 384 px |
| image pixels | 64 × 1024 × 1024 |

Exceeding a limit is an error (`E_MANIFEST_TOO_LARGE`, `E_FILE_TOO_LARGE`,
`E_TOTAL_TOO_LARGE`, `E_TOO_MANY_FILES`, `E_IMAGE_TOO_LARGE`).

## Diagnostics

Validation returns a list of issues. Each issue has a stable `code`, a JSON-pointer-like
`location` (`pack.json#/assets/0/path`, `file:assets/bg.png`), a human readable message and
a `severity` (`error` or `warning`). Errors block `validate`/`export`; warnings do not.

`validate --json` prints:

```json
{
  "ok": false,
  "errors": [{"code": "E_HASH_MISMATCH", "location": "pack.json#/files/0/sha256", "message": "...", "severity": "error"}],
  "warnings": [],
  "file_count": 1
}
```

Exit codes: `0` valid / exported, `1` validation or export failure, `2` command-line usage
error (argparse).

## Schema version policy

- Adding an optional field, a new warning code, or relaxing a check bumps the **minor**
  version; existing 1.0 manifests keep validating.
- Removing/renaming a field, changing its type, or making a previously optional field
  required bumps the **major** version.
- This tool implements 1.0.x and rejects any other major, or a minor newer than 0, with
  `E_SCHEMA_UNSUPPORTED`. The patch number does not affect compatibility.
- A future consumer must check `schema_version` before trusting fields it does not know.

## Export archive

`export` first validates the pack and refuses to write anything for an invalid pack. The
archive is written to a temporary file in the destination directory, re-opened and verified,
then moved into place with `os.replace`:

- members: `pack.json` first (raw source bytes), then every declared file sorted by path;
- no directory entries, no undeclared files;
- `ZIP_DEFLATED`, level 9, fixed DOS timestamp `1980-01-01 00:00:00`, `create_system = 3`,
  mode `0644` — the same input therefore produces byte-identical archives;
- verification: CRC (`testzip`), duplicate member names, exact member set, and SHA-256 of
  every member against its source;
- an existing output is never replaced unless `--overwrite` is passed
  (`E_OUTPUT_EXISTS`); writing the archive inside the pack root is refused
  (`E_EXPORT_INSIDE_PROJECT`);
- any failure removes the temporary file and leaves an existing output untouched.

Default output name when `-o` is omitted: `<pack_id>-<version>.zip` in the current
directory.

## Relationship to the triage contract

The pack contract does not consume triage JSON and does not modify it. Triage evidence IDs
(`E1..En`) and line numbers remain owned by `triage_agent.py`; packs are not evidence and
must not claim to be. A future Skill may read a pack manifest, but only this document
defines what it may trust.

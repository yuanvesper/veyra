# Phase 2.5 / SafeWriter review evidence

**Status:** evidence publication only. This does **not** mean SafeWriter is production-ready.

**Baseline:** `main` at `88c5107707cc2b58cead814c14b005bb3af7a11b`
(`feat: implement AI creation workflow and secure file operations`,
2026-11-21 22:33:09 +0800). The evidence was assembled from the repository at that
commit plus the untracked SafeWriter prototype and tests then present in the working
tree.

## Purpose

P0-08 records that external reviewers could not reproduce the Phase 2.5R / SafeWriter
review because the design, prototype source, and focused tests were absent from GitHub.
This directory publishes the minimum reviewed evidence needed to read the design and
run the focused prototype tests:

- the Phase 2.5R.1 design, as a sanitized copy;
- the ten SafeWriter prototype modules plus the package initializer under
  `veyra_creation/safewriter/`;
- the focused prototype test file at `tests/test_creation_safewriter.py`.

The original design file is not published because it contains machine-specific absolute
paths. Its content is otherwise preserved in the sanitized copy.

## Contents

| Path | What it is |
|---|---|
| [`design/phase-3.2.2.1-2.5R.1-production-safewriter-admission-design.sanitized.md`](design/phase-3.2.2.1-2.5R.1-production-safewriter-admission-design.sanitized.md) | Sanitized copy of the Phase 2.5R.1 production SafeWriter admission design. |
| [`../../veyra_creation/safewriter/`](../../veyra_creation/safewriter/) | Prototype source: `anchors.py`, `errors.py`, `identities.py`, `journal.py`, `lock.py`, `native.py`, `policy.py`, `results.py`, `target.py`, `writer.py`, `__init__.py`. |
| [`../../tests/test_creation_safewriter.py`](../../tests/test_creation_safewriter.py) | Focused real-filesystem tests for the prototype. |
| [`MANIFEST.sha256`](MANIFEST.sha256) | SHA-256 values for the published evidence and for the local originals. |

## Reproduce the focused tests

From the repository root, on Windows:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
python -m unittest -v tests.test_creation_safewriter
```

Observed for this publication:

- Python 3.14.7 on Windows;
- `Ran 26 tests in 0.575s`;
- `OK`;
- exit code 0.

This is a record of one environment, not a cross-version or cross-machine guarantee.
The test suite uses a private temporary project root; it does not write to `report.md`,
`real_crash.log`, or the repository root.

## Review status (do not overread this)

The published design self-declares:

- **Recommendation:** `READY FOR INDEPENDENT RE-REVIEW`;
- **Production admission:** `BLOCKED`;
- owner decisions **D-01**, **D-11**, and **P0-01** remain **OPEN**;
- the B-06 same-user hardlink race remains open under D-01;
- the power-loss claim in Section 9.6 remains **UNVERIFIED**.

Publishing this evidence is not an admission decision, a production-readiness claim, or
a substitute for the independent re-review. The focused tests are prototype regression
tests, not the admission suite described in the design.

## Sanitization

Local absolute paths were replaced with placeholders; no other content was changed.
The substitutions were:

| Original value | Placeholder |
|---|---|
| Repository path | `<repo-root>` |
| Parent workspace path | `<workspace-parent>` |
| Escaped repository path | `<repo-root-escaped>` |
| Case-normalized repository key | `<repo-root-key>` |
| Explicit Python 3.15 executable | `<python-3.15>` |
| MSYS2 Python 3.14 executable | `<python-3.14-msys2>` |
| Drive root in a code example | `<drive>:\` |

Generic examples such as `C:\foo\..\bar`, `C:foo`, and `\\?\C:\...` are retained because
they are path-syntax examples, not machine-specific paths. The sanitized copy contains no
`C:\Users\...`, `C:\msys64\...`, or `D:\Projects_vesper\...` values.

Original and sanitized hashes:

- original design (local working copy, CRLF): `13BA6C6A8A08DC957689DECD0ECC8E766B9072BEF6F232692F4605F440B0CA15`
  (251,727 bytes);
- sanitized published design (LF): `000AE6EAAFF4027C212B96A46334896DEC91991FC942411C84DBCA16E2F6D3D5`
  (249,023 bytes).

Git normalizes line endings on commit. The local original uses CRLF; the published
sanitized copy is LF. Line content is unchanged.

## Excluded material

- `report.md` and `real_crash.log` were not read, modified, staged, or committed. Their
  SHA-256 values were verified only as metadata.
- The other Phase 3.2.2.1 design and review documents remain unpublished in this change.
  They contain local environment paths, and publishing a second sanitized corpus was not
  required for the focused review. The R.2 / R.2.1 planning records were chat-only and are
  not repository artifacts.
- No real player logs, credentials, tokens, or private data are included.

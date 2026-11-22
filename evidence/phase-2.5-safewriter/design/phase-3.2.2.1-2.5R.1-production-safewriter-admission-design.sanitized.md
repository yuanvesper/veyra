# Phase 3.2.2.1-2.5R.1 - Production SafeWriter Admission Design

**Document ID:** `VEYRA-PHASE-3.2.2.1-2.5R.1`  
**Revision:** `2.5R.1` (2026-11-22) - Independent design-review remediation. This revision preserves the 2.5R document as a historical review input and supersedes only the sections listed in Section 1.6 and Appendix H.  
**Baseline:** the 2.5R design (`docs/phase-3.2.2.1-2.5-production-safewriter-admission-design.md`, SHA256 `a99ddc38d00b2ece0a6b69c38a1e56dfa4de0c4653f4380419827cf23f9c8228`) and its change report (`docs/phase-3.2.2.1-2.5R-design-consistency-change-report.md`, SHA256 `e461841c3a2de4197d5fec78033a95876e2135f495b07c152e6de28d3aec8e81`).  
**Status:** design remediation only. No production implementation, no ProjectStore/CLI integration, no source-code or test changes, no Git writes, no Phase 2.6 work.  
**Baseline revision:** `main` at `88c5107707cc2b58cead814c14b005bb3af7a11b`; working tree state is recorded in Section 2.2.  
**Date:** 2026-11-22.  
**Language:** English, as required for this phase. Existing Chinese design documents are not modified.

**Recommendation:** **READY FOR INDEPENDENT RE-REVIEW**.  
**Production admission:** **BLOCKED**. This is a design input, not an admission decision. Owner decisions D-01, D-11, and P0-01 remain OPEN; the B-06 same-user hardlink race remains OPEN under D-01, and the power-loss claim in Section 9.6 remains UNVERIFIED. No P0 gate is PASS because this document specifies it.


---


## 1. Executive summary

### 1.1 What this document is

This is the production design that must be accepted before Veyra's SafeWriter can be used for real project files. It answers six questions that the 2.4 prototype did not answer:

1. How is the trust root established, and how is every later path use re-bound to a live handle?
2. How is a single writer enforced, including same-process re-entry, cross-process contention, stale lock files, and lock-file replacement?
3. How are `creation/`, `operations/`, the journal, the lock, quarantine, temporary files, and the target revalidated, so a successful result cannot be reported for a project path that has been swapped?
4. How is a failed `replace` compensated, and when is compensation forbidden because it would overwrite or delete unknown content?
5. What journal transitions, flush points, torn-write rules, and recovery classifications are required for crash and power-loss analysis?
6. Which Windows guarantees are documented, which were reproduced, and which are still unverified or unverifiable on this machine?

The document deliberately separates **what the design requires** from **what the current prototype implements**. A design requirement is not evidence that the code meets it. The 2.4 prototype is a successful experiment; it is not an admission candidate.

### 1.2 The seven corrections that drive this design

These are the material differences from the 2.3 design and the 2.4 prototype. Each is supported by code inspection, an experiment, or both.

| ID | Correction | Why it matters |
|---|---|---|
| C-01 | Directory anchors must open with `FILE_SHARE_READ \| FILE_SHARE_WRITE` and **must not grant `FILE_SHARE_DELETE`**. The prototype grants `FILE_SHARE_ALL` at `anchors.py:139`, `:305`, `:316`, `:326`. | With `FILE_SHARE_DELETE`, an external rename of the root or a business parent can succeed while the operation is live. The prototype's own report attributes the observed rename failure to directory handles; the experiment shows the real blocker is a live descendant file handle with a restrictive share mode. Denying delete on directory handles makes the path stable for the session. |
| C-02 | Hardlink creation is **not** blocked by `FILE_SHARE_READ`, `FILE_SHARE_WRITE`, `FILE_SHARE_DELETE`, or the read-only attribute. The prototype already checks `nlink`, but it checks only at open/adoption points. | A hardlink can be created while the file is open. Checks reduce the window but cannot eliminate it without ACL/account isolation. The design must state this residual risk honestly and fail closed. |
| C-03 | Binding revalidation must include `creation`, `operations`, the journal, the lock, and the quarantine anchor, not only the root and the business parent (`writer.py:626-656`). | A second process can create a new `creation/` directory and a new lock while the first process still reports success against a now-invisible project path. Internal writes must be bound to the same session anchors. |
| C-04 | A failed `replace` must have an explicit compensation path. The prototype returns `RECOVERY_REQUIRED` after a final rename failure and leaves the old target in quarantine (`writer.py:388-489`). | "Backup before delete" is not enough. If the commit fails and the target path is still absent, the old content can be restored safely; if it cannot, the result must be uncertain, not successful. |
| C-05 | Journal v2 needs explicit record framing, size/offset verification, a documented tail-repair policy, and a distinction between a torn tail and corruption. The prototype repairs a missing trailing newline (`journal.py`) while its docstring calls that corruption. | A crash between `write` and `flush`, a disk-full append, and a truncated record must be classified deterministically. Silent repair can hide an intent record. |
| C-06 | Root trust must be pinned by an explicit, per-user trust record or a caller-supplied identity. The prototype accepts `expected_root_identity` but does not require it for first use. | Opening a path and then recording its identity is trust-on-first-use. If the path was already swapped, the identity is the attacker's identity. First-use enrollment must be an explicit user action. |
| C-07 | The prototype test suite is not an admission suite. The 2.4 audit found 21 `def test_` names but 20 methods and 19 unique behaviours, a duplicate test, and a false-positive temp-write test. Phase 2.4R closed the duplicate and repaired the temp test; the suite now has **26 unique tests**. It still passes when `_verify_binding` is replaced by a no-op, and has no test for a hardlink created after open, replace compensation, or an internal-anchor swap. | A green suite still proves less than an admission result. Admission needs tests that fail when the invariant fails, not tests that happen to pass. |

### 1.3 Verdict in one paragraph

The architecture is coherent enough to review: one trust root, handle-relative traversal, identity-based anchors, one OS lock, intent-before-side-effect, quarantine-before-overwrite, and explicit recovery. It is not production-ready. Phase 2.4R closed the known handle leaks, the Unicode escapes, the duplicate test, and the false-positive temp test; this revision starts from that corrected baseline. The remaining P0 work is still not a small patch: directory share modes must be tightened; every internal anchor must be re-bound; the hardlink race must be represented as an explicit limitation, not as eliminated protection; replace compensation must be implemented; journal v2 and recovery classification must be implemented; the test suite must gain mutation and adversarial coverage. Power-loss durability and symlink/junction refusal across Windows builds are not proven on this machine.

This revision also fixes a category error in the earlier text: SafeWriter does not establish a security boundary against a malicious process running as the same Windows user. It is an integrity protocol against non-malicious non-cooperating writers and, where ACLs deny write, other low-privilege users. A determined same-user process can write the project directly, create hardlinks, or edit the trust record; SafeWriter can detect some of those changes and fail closed, but it cannot prevent them. The design is reviewable only with that boundary stated explicitly.

### 1.4 What this document does not do

- It does not modify `triage_agent.py`, `veyra_creation/`, `tests/`, `skills/`, `README*`, or any existing design.
- It does not integrate SafeWriter into `ProjectStore`.
- It does not implement `delete`, backup, or recovery.
- It does not run a power-loss test, create a real symlink, enable Developer Mode, elevate privileges, or read/write protected files.
- It does not claim production admission.

### 1.5 What revision 2.5R changes

Revision 2.5R is a consistency and security-clarification revision, not a new architecture. It makes the following corrections; the detailed change list is in the companion change report and Appendix H.

| ID | 2.5R correction | Where |
|---|---|---|
| R2-01 | P0 gates now have one lifecycle vocabulary and an explicit gate-to-phase mapping. 2.6 no longer claims gates that are deferred to 2.7, 2.8, or 2.9. | Section 9.8, Section 10.2-10.5 |
| R2-02 | The threat model distinguishes non-malicious non-cooperating writers, malicious same-user processes, different low-privilege users, and administrators/kernel/physical attackers. SafeWriter claims no same-user security boundary. | Section 3.2, Section 3.3, Section 11.2, Section 11.4 |
| R2-03 | The handle-leak criterion is one measurable procedure: 10 warm-up iterations, a five-sample baseline, 100 measured iterations, and a named leak condition. The older 25-iteration evidence is diagnostic only. | Section 9.1, T-43, Section 10.2 |
| R2-04 | The root trust record has a versioned schema, exact identity fields, explicit enrollment, ACL verification, and defined behavior for moves, volume changes, corruption, copying, rollback, and tampering. | Section 5.3, T-46-52 |
| R2-05 | Replace and recovery have transition tables covering failure before and after each side effect, quarantine mismatch, final rename failure, rollback failure, process termination, idempotence, and unidentifiable objects. | Section 7.3.3, Section 7.6, Section 7.7.1 |
| R2-06 | Hardlink checks are described as observed-link-count refusals, not race-free prevention. D-01 is an exception, never a PASS, and is compatible only with a threat model that excludes malicious same-user processes. | Section 3.4 I-04, Section 6.3, Section 11.1, Section 11.3 |
| R2-07 | The document separates prototype regression tests from admission tests, and maps every P0 gate to tests, mutation checks, and evidence artifacts. | Section 9.1.1, Section 9.3, Section 9.8.4 |
| R2-08 | The original 2.5 document identity is preserved through this revision block, Appendix H, and a separate English change report. | Header, Appendix H |

### 1.6 What revision 2.5R.1 changes

This revision is a **review remediation**, not a new architecture. It closes the specific design contradictions identified by the independent review, while preserving every conclusion that remains valid. It does not implement, test, or admit anything.

| Review finding | Remediation in R.1 | Primary sections |
|---|---|---|
| R1 - Incomplete Create recovery | A normative Create transition table, a ten-case Create recovery table, explicit `COMMIT_INTENT + target absent + temp intact` handling, Create metadata adoption, and bounded parent-directory cleanup. | Section 7.2.1-7.2.5, Section 7.6, Section 7.7.2 |
| R2 - Journal append vs. Replace compensation | One invariant: no content side effect without a durable intent. A six-case compensation table and a state-dependent `E_JOURNAL_APPEND` mapping. | Section 7.3.1, Section 7.8 |
| R3 - Journal v2 framing | One canonical length contract (64 KiB including LF), exact formulas, shared append/read/repair validation, and an atomic tail repair that includes its own record. | Section 7.5.1-7.5.5 |
| R4 - Result/error taxonomy | Five canonical statuses; `OK` removed from the public surface; explicit status/error/journal/durability/retry relationships; complete glossary. | Section 4.8, Section 7.8, Section 12.3 |
| R5 - Crash vs. power-loss gates | `T-38` is process crash, `T-39` is power loss (BLOCKED without a disposable VM), and `T-53` is create recovery. Phase 2.7 and 3.0 no longer conflate them. | Section 9.3, Section 9.6, Section 9.8, Section 10.3, Section 10.6 |
| R6 - P0-09 EXCLUDED semantics | An exclusion contract, forbidden surfaces, surface-verification test, reopening event, and a rule that exclusion is not PASS. | Section 7.4.1, Section 9.8.2-9.8.4, Section 10.6 |
| R7 - B-06 hardlink evidence conflict | B-06 is a B2 same-user malicious experiment, not a B3 justification; B3 is handled fail-closed. D-01 remains OPEN. | Section 1.2, Section 12.2, Section 11.3 |
| R8 - Incomplete gate-to-test map | Every P0 gate has explicit test IDs, level, expected status, filesystem/journal assertions, fault injection, evidence artifact, and phase owner. | Section 9.8.4 |
| R9 - Phase scope vs. exit criteria | R-19..R-27 have an owner, target phase, test/evidence, and closure condition; L5 adversarial tests are assigned. | Section 8.5, Section 10.2-10.4 |
| R10 - Evidence schema | A versioned JSONL evidence schema with environment, identities, journal, handles, hashes, and framing rules. | Section 9.7 |
| R11 - Durability classification | Four distinct durability failures with defined status and claim consequences. | Section 6.7.1, Section 4.8, Section 7.8 |
| R12 - Journal archival | Archive metadata, chain handoff, crash behavior, verification, and safe `E_JOURNAL_FULL` behavior; archival remains a future implementation. | Section 7.5.6, Section 11.3 D-05 |

**Supersession map.** The 2.5R document remains the historical baseline. R.1 replaces the following 2.5R sections in full: Section 1.5 (superseded by this Section 1.6), Section 4.8, Section 6.7.1 (new), Section 7.2, Section 7.3.1, Section 7.5.1-7.5.4, Section 7.6-7.8, Section 8.5 (new), Section 9.3 rows T-38/T-39 and appended tests, Section 9.6-9.7, Section 9.8.2-9.8.4, Section 10.2-10.3, Section 10.6, Section 11.3 D-05, Section 12.2 B-06, Section 12.3, Section 12.5-12.8. All other sections are carried forward unchanged except for cross-references. Where a carried-forward sentence conflicts with a replaced section, the replaced section controls.

---


## 2. Evidence, baseline, and protected files

### 2.1 Evidence grades

Every non-obvious claim below is labelled with one of these:

- **[DOC]** Microsoft documentation, cited in Section 12.
- **[EXP]** a reproducible experiment run outside the repository, recorded in Section 12.2.
- **[CODE]** direct inspection of the current repository at the baseline commit.
- **[TEST]** an executed test or probe whose output is recorded here.
- **[INF]** an inference from the above; not directly observed.
- **[UNVERIFIED]** not tested on this machine and not admitted as a guarantee.

A **[DOC]** statement is not automatically a guarantee for every Windows build, filter driver, or filesystem. A **[EXP]** result is true on the recorded OS, filesystem, Python, and account; it must be repeated on the admission matrix in Section 9.

### 2.2 Repository baseline

- Repository: `<repo-root>`.
- Branch: `main`.
- HEAD: `88c5107707cc2b58cead814c14b005bb3af7a11b` (`feat: implement AI creation workflow and secure file operations`, 2026-11-21 22:33:09 +0800).
- `git status --short` at design time:
  - ` M report.md`
  - untracked: `docs/phase-3.2.2.1-1-security-boundary-decision.md`, `docs/phase-3.2.2.1-2.0-experimental-admission-plan.md`, `docs/phase-3.2.2.1-2.1-e1-handle-security-report.md`, `docs/phase-3.2.2.1-2.1R-e1-residual-closure.md`, `docs/phase-3.2.2.1-2.2-e1-final-residual-verification.md`, `docs/phase-3.2.2.1-2.3-production-safewriter-admission-design.md`, `docs/phase-3.2.2.1-2.4-independent-audit.md`, `docs/phase-3.2.2.1-2.4-safewriter-prototype-report.md`, `docs/phase-3.2.2.1-2.4R-independent-reaudit.md`, `docs/phase-3.2.2.1-2.4R-safewriter-remediation.md`, `docs/phase-3.2.2.1-2.5-production-safewriter-admission-design.md`, `tests/test_creation_safewriter.py`, `veyra_creation/safewriter/`.
- No commit, add, push, reset, checkout, clean, rebase, or history rewrite was performed in this phase.

### 2.3 Protected files

Only hashes were read; contents were not opened or written.

| File | SHA-256 | State |
|---|---|---|
| `report.md` | `A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D` | modified before this phase; never touched |
| `real_crash.log` | `CC1418B7690284787B55D3417383F23DD6DEAA47619BC41702F9F33895DCD54D` | unchanged; never touched |

`report.md` is the user's uncommitted analysis output and is treated as a protected, opaque file. No test may write to it, and no command in this phase references its contents.

### 2.4 Environment

| Item | Value |
|---|---|
| OS | Microsoft Windows 11 Home, Chinese, version 10.0.26300, build 26300, 64-bit |
| Filesystem | `C:` NTFS, fixed disk, 429,497,774,080 bytes total |
| Python (experiments) | 3.15.0 (tags/v3.15.0:d220409, Oct 9 2026), MSC v.1951 64-bit, explicit `<python-3.15>` |
| PowerShell | 5.1.26100.9818 |
| Tested account | current interactive user, non-elevated; symlink creation denied |
| Default `python` on PATH | 3.14.7 (MSYS2 UCRT64, `<python-3.14-msys2>`); not the interpreter used for the Windows experiments |
| Cross-version tests | 3.15.0: `353 tests, OK (skipped=4)`; 3.14.7 with `PYTHONUTF8=1`: `353 tests, OK (skipped=4)` |

The `C:` volume is the experiment volume. All experiments used `tempfile.mkdtemp()` under `%TEMP%`; no experiment root was created inside the repository.

---

## 3. Threat model and security invariants

### 3.1 Assets

1. Project files under the project root, including user-authored source, assets, configuration, and logs.
2. The absence of a file at a target name (creating over an unknown file is a loss).
3. The old content of a replaced file, preserved in quarantine until an explicit purge.
4. The journal, lock, quarantine index, and operation metadata, because they determine whether recovery is allowed.
5. The truthfulness of the returned result: `SUCCEEDED` must mean the intended object, at the intended path, with the intended bytes, and no unknown object was overwritten or deleted.
6. The user's trust that `report.md` and other protected files are not touched.

### 3.2 Actors, failure sources, and the security boundary

The actor categories below are the decision-relevant ones. The failure-source table that follows classifies *how* a fault enters the protocol; it is not a claim that every source is an attacker.

| ID | Actor | Security boundary? | In scope? | What SafeWriter can do | What it cannot do |
|---|---|---|---|---|---|
| B1 | Non-malicious, non-cooperating writer (editor, sync client, build tool, backup tool, second Veyra process that ignores the lock) | No | **Yes, as a failure source.** | Detect identity/content changes at checkpoints; refuse; fail closed; preserve user content; report a truthful status. | Prevent the writer from writing the project directly; guarantee that every race is observed. |
| B2 | Malicious process running under the same Windows user identity | **No.** There is no account, ACL, integrity-level, or kernel boundary between the process and SafeWriter. | **No, not as a security guarantee.** | Detect some directory/target substitutions and observed hardlinks; fail closed; avoid following a path into an unknown object. | Prevent direct project writes, hardlink creation, trust-record edits, handle duplication, debugger attachment, or a race won between checkpoints. Do **not** claim protection here. |
| B3 | Different low-privilege Windows user | Only if the project ACL denies write/delete/create-child to that user. | **Only under that ACL condition.** | Rely on the ACL for prevention; detect a violation; refuse on sharing/ACL failure. | Create a boundary the ACL does not provide. If the ACL grants write, treat that user as B2 for the objects they can modify. A hardlink created by B3 cannot bypass the file's ACL for writes, but it can raise `nlink` and cause a refusal/denial of service. |
| B4 | Elevated administrator, kernel-level attacker, physical attacker, ransomware with backup/restore privileges | No | **No.** | Nothing beyond fail-closed behavior and preserving evidence. | Resist privilege, kernel, memory, backup, or physical compromise. |
| B5 | Crash, process termination, power loss | No | **Crash consistency: yes. Power-loss durability: limited.** | Journal, classify, recover only provable states; never report silent success. | Guarantee directory-entry durability across power loss; that requires VM evidence and remains UNVERIFIED. |

**Boundary statement.** SafeWriter is an integrity protocol, not a same-user security boundary. Its strongest claim is: *for a local NTFS project whose ACL denies write to other users, and against non-malicious concurrent writers, a successful result identifies the intended object and no unknown object is silently overwritten or deleted.* It does not claim that a malicious same-user process cannot modify the project. If the product security objective includes B2, this design is insufficient and must return to architectural redesign (Section 11.3 D-01/D-11).

The following table classifies failure sources used elsewhere in this document.

| ID | Failure source | Capability | In scope? |
|---|---|---|---|
| A1 | Local other user / process with ACL write | Can create/rename/delete files in the project tree if ACLs allow; can create junctions, symlinks, hardlinks; cannot inject code into the Veyra process. | Only as B1/B3. If the ACL grants write, that actor is B2-equivalent for those objects. |
| A2 | Same-user malicious process | Can read/write the project and call any Win32 API as the user, including creating hardlinks and replacing directories. | **No security guarantee.** Detection and fail-closed only; see B2. |
| A3 | Accidental concurrent tool | Editor, sync client, build tool, or second Veyra process writing concurrently. | Yes, as B1. |
| A4 | Crash / power loss | Process kill, bugcheck, power cut at any instruction. | Yes for crash consistency; durability limited to Section 6.7. |
| A5 | Reparse object | Junction, symlink, mount point, cloud placeholder, unknown reparse tag. | Yes; rejected. |
| A6 | Hardlink | A second directory entry for the same file on the same volume. | Yes as a detected condition and as a B2 residual; not prevented. |
| A7 | Antivirus / EDR / indexer | Opens files, scans, holds handles, causes sharing violations or latency. | Yes, as B1; fail closed. |
| A8 | Disk full / quota / ACL denial | Writes fail part-way; metadata operations fail. | Yes. |
| A9 | Path/name confusion | `..`, ADS (`:`), reserved names, trailing dots/spaces, Unicode surrogates, case collisions, long paths. | Yes; rejected or normalized deterministically. |
| A10 | Supply chain / ctypes ABI bug | Wrong struct layout, wrong HANDLE width, wrong status sign. | Yes; architecture and runtime checks. |
| A11 | Remote / removable / non-NTFS volume | SMB, ReFS, FAT, cloud-synced folder. | **Out of scope**; reject. |
| A12 | Kernel attacker / physical access / ransomware | Can subvert the OS or read memory. | **Out of scope**; see B4. |

### 3.3 Trust assumptions

The design is valid only while all of these hold:

1. **Security objective.** The objective is integrity against B1 and against B3 only where the project ACL denies write/delete/create-child. B2 (malicious same-user code), B4 (elevated/kernel/physical), and a B3 actor with ACL write are outside the guarantee. If the owner requires protection against B2, this design is not admissible; see Section 11.3 D-01/D-11.
2. **Volume and path.** The project root is on a local fixed NTFS volume, addressed by an absolute drive-letter path; no UNC, device path, subst, network drive, removable volume, ReFS, or FAT.
3. **Root stability.** The project root and its ancestors are not reparse points and are not replaced during a session. The design enforces this after the root chain is opened; the first-open window is covered only by the trust record in Section 5.3, not by a cryptographic attestation.
4. **ACL.** The project root ACL gives write/delete/create-child rights only to the operating user and to principals the user trusts. If another user can write any object, that user is B2-equivalent for that object. A hardlink created by another user cannot bypass the file ACL for writes, but it can raise `nlink` and cause refusal.
5. **Process integrity.** No untrusted code runs inside the Veyra process. Same-user malware is outside the guarantee; it can already read and write the project directly and can edit the trust record.
6. **Windows semantics.** The Windows kernel, NTFS, and the Python/ctypes layer behave as documented and as reproduced in Section 12.2. A filter driver may add latency or sharing violations; those are handled as failures, not as success.
7. **Time.** Time is used for logs and recovery age, not for correctness. A clock jump must not change whether a write is allowed.
8. **Trust record.** The trust record in Section 5.3 is stored outside the project tree, is owner-only ACL-protected, and is not writable by other users. The record is a consistency and anti-accident control, not a tamper-proof same-user security control.
9. **Hardlink semantics.** Share modes do not prevent hardlink creation. `nlink` checks establish only the link count observed at a checkpoint; they do not prevent a link created between checkpoints. The residual is accepted only as the B2 limitation described in Section 11.1 X-01 and Section 11.3 D-01.

If assumption 3, 4, 8, or 9 is false for the declared scope, the correct behavior is `REFUSED` before any side effect, not a best-effort write. If the objective includes B2, the correct behavior is to stop and redesign, not to add another checkpoint claim.


### 3.4 Security invariants

These are the admission invariants. Each has a test in Section 9.

| ID | Invariant | Violation is |
|---|---|---|
| I-01 | No write, rename, delete, or metadata change outside the opened project root. | P0 |
| I-02 | No reparse point is followed. A reparse object at any component or target is refused before a side effect. | P0 |
| I-03 | Every target operation is bound to the exact `(VolumeSerialNumber, FileId)` observed at admission. A path alone never authorizes a write. | P0 |
| I-04 | A target/temp/quarantine with `nlink != 1` observed at a checkpoint is refused or classified uncertain; it is never silently ignored. The check establishes the observed link count only; it does **not** prevent a link created after the checkpoint. A link created between checkpoints is a declared B2 residual, not eliminated protection. | P0; D-01 exception decision |
| I-05 | A create never overwrites an existing name. A replace only replaces the exact admitted target and never an unknown object. | P0 |
| I-06 | A journal intent record is durable before the side effect it describes. No side effect relies on an in-memory intention. | P0 |
| I-07 | The old content of a replaced or deleted file is in quarantine before it is removed from its original name. | P0 |
| I-08 | At most one writer holds the project lock; lock identity cannot be swapped while held. | P0 |
| I-09 | Every internal path (`creation`, `operations`, journal, lock, quarantine, temp) is bound to the same session anchors; a path re-open is never trusted by path alone. | P0 |
| I-10 | Every failure is fail-closed: no silent downgrade to best-effort, no automatic delete of unknown content, no success result with an unmet postcondition. | P0 |
| I-11 | Recovery operates inside the same root/target boundary and defaults to read-only diagnosis; automatic content adoption is limited to the cases in Section 7.7. | P0 |
| I-12 | Durability is not claimed beyond what was flushed and verified; directory-flush success is not a power-loss guarantee. | P1 |
| I-13 | The result status distinguishes `SUCCEEDED`, `REFUSED`, `FAILED`, `RECOVERY_REQUIRED`, and `COMMIT_UNCERTAIN`; warnings never masquerade as success. | P0 |
| I-14 | Protected files (`report.md`, `real_crash.log`, and any caller-declared protected path) are never opened for write and never renamed/deleted. | P0 |
| I-15 | Names and paths are validated before native calls: no unpaired surrogate, no ADS, no reserved device name, no trailing dot/space, no `..` component, no overlong component, no unsupported volume. | P0/P1 |

### 3.5 Non-goals

- Protecting against a user who deliberately runs a second process that ignores the lock. The lock is cooperative; the identity checks prevent that process from causing *this* process to write the wrong object, but they do not stop it from writing directly.
- Protecting against a same-user process that creates hardlinks after the last checkpoint. See Section 11.1 (X-01).
- Guaranteeing power-loss durability of directory entries on every NTFS build. See Section 6.7.
- Encrypting quarantine or backups. This is a confidentiality follow-up, not a correctness gate for 2.5.
- Cross-platform filesystems, network shares, cloud placeholder semantics, or ReFS.
- A full transactional multi-file commit. SafeWriter commits one target at a time; the journal records one operation. Multi-file project transactions need a higher-level protocol and are out of scope.

---

## 4. Architecture

### 4.1 Layers and dependency direction

```
CLI / ProjectStore / future Skill tool
                |
        SafeWriter facade           (public contract, result mapping)
                |
   +-------------+--------------+--------------+
   |             |              |              |
Operation    LockManager   RecoveryManager  (future)
   |             |              |
   +---- OperationJournal ----------------+
   +---- Backup/Quarantine ---------------+
   +---- TargetAnchor --------------------+
   +---- DirectoryAnchor -----------------+
                |
            RootAnchor
                |
             native.py         (NtCreateFile, NtSetInformationFile, ...)
                |
          policy.py            (pure validation; no I/O)
```

Rules:

- `policy.py` is pure: it must not touch the filesystem. It rejects invalid input before any handle is opened.
- `native.py` is the only module allowed to call Win32/NT APIs. It must not make policy decisions; it validates struct sizes and raises typed errors.
- `anchors.py` owns directory handles. It never accepts a path for a write after the session is open.
- `target.py` owns file handles. It never opens by path after admission; it uses the held parent handle.
- `writer.py` owns the state machine. It never calls `os.rename`, `os.remove`, `shutil`, or `open()` for project content.
- `journal.py` and `lock.py` are the only modules that may open their own files, and only through an anchored parent.
- `recovery.py` (future) uses the same anchors and journal; it is not a separate path-based repair tool.

### 4.2 Proposed package layout

The 2.4 prototype already has `veyra_creation/safewriter/{native,policy,anchors,target,journal,lock,writer}.py`. Keep that layout; add only:

```
veyra_creation/safewriter/
  result.py        # OperationResult, status, error codes; no I/O
  recovery.py      # future; diagnosis first, explicit apply
  trust.py         # future; per-user root trust record (outside project)
```

Do not rename `creation/` to `.veyra/` in 2.5. `ProjectStore` already treats `creation/` as its reserved state directory (`veyra_creation/store.py:260-268`). A rename would be a migration with its own risk and is not needed for correctness. The internal state is:

```
<project-root>/
  creation/
    state.json, spec.json, plan.json, decisions.json, locks.json, ...
    executor.lock                    # single-writer lock
    operations/
      <batch_id>.journal.ndjson      # one operation journal
      tmp/                           # private temp staging (new)
    quarantine/
      <batch_id>/                    # old content, one directory per operation
```

`creation/` is reserved. If a project already has a non-state `creation/` directory, `ProjectStore.initialize` already refuses to initialize over it (`store.py:296-299`); SafeWriter must apply the same rule, not write beside unknown content.

### 4.3 Core objects

| Object | Owns | Must never | Key invariant |
|---|---|---|---|
| `RootAnchor` | Volume root handle and every component handle from volume root to project root, plus `RootIdentity` and expected identity | Use `os.path.realpath`, follow a reparse point, or re-open a path for a write | The chain is stable; the final identity equals the trusted identity; all handles close on every failure path |
| `DirectoryAnchor` | One directory handle, its `(volume serial, file id)`, and its parent anchor | Trust a path string; open a child by path; grant `FILE_SHARE_DELETE` | Child opens are handle-relative and reparse-rejecting |
| `TargetAnchor` | One existing file handle, identity, size, hash, `nlink`, and share mode | Open by path after admission; ignore `nlink`; grant write/delete to others | File identity and content are unchanged at every checkpoint |
| `LockManager` | `executor.lock` handle, owner token, lock byte | Delete/rename the lock file while held; trust a path re-open | Lock is held for the whole session; identity is verified |
| `OperationJournal` | Journal handle, sequence, hash chain, append offset | Repair a complete-but-invalid record; continue after a failed append | Every intent is durable before its side effect |
| `QuarantineManager` | Quarantine directory anchor and per-operation names | Overwrite an existing quarantine entry; delete without an explicit purge | Old content is preserved and addressable |
| `SafeWriter` | Session lifecycle and the state machine | Return success before all postconditions; fall back to best-effort | The result status matches the filesystem state |
| `RecoveryManager` | Read-only diagnosis and explicit apply | Auto-delete unknown content; operate outside the root | Only Section 7.7 cases may be applied automatically |

### 4.4 Handle ownership and close rules

Every object that opens a handle owns it. A caller may transfer ownership only through an explicit `close()`/`detach()` method. The session has a close order:

1. journal (flush, then close)
2. quarantine/temp handles
3. lock (unlock, then close)
4. operations anchor
5. creation anchor
6. business parent anchors
7. root chain (reverse order)
8. volume root handle

If an exception occurs before the object is returned to its owner, the constructor/factory must close what it opened. The current prototype violates this in several places; Section 8.1 lists them. The production rule is:

> A function that opens more than one handle must have a single `try/finally` whose `finally` closes every handle not transferred. `except` blocks may translate the error but must not be the only cleanup path.

### 4.5 Anchor opening

#### 4.5.1 Root path

Input: canonical absolute local path, e.g. `<repo-root>`, plus a trusted `RootIdentity`.

Algorithm:

1. `policy.validate_root_path`:
   - absolute drive-letter path, no UNC/device/ADS;
   - components are simple names; no `.` or `..` (reject before normalization, not after);
   - no trailing dot/space; no reserved device name; no unpaired surrogate;
   - length within the supported extended-path limit;
   - volume facts: `DRIVE_FIXED` and `NTFS`.
2. `native.open_volume_root("<drive>:\\")` with desired access `FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE`, share `FILE_SHARE_READ | FILE_SHARE_WRITE`, `FILE_FLAG_BACKUP_SEMANTICS | FILE_OPEN_REPARSE_POINT`.
3. For each component, in order:
   - `native.nt_open_relative(parent, component, desired=ancestor or final access, share=READ|WRITE, disposition=FILE_OPEN, options=FILE_DIRECTORY_FILE|FILE_OPEN_REPARSE_POINT, attributes=0)`;
   - query attributes and reparse tag; if `FILE_ATTRIBUTE_REPARSE_POINT` or any tag, close and refuse `E_REPARSE_REFUSED`;
   - query identity; require same volume serial as the volume root;
   - keep the handle in the chain (do not close it after opening the next component).
4. Compare the final identity to the trusted identity. On mismatch, close the whole chain and return `E_ROOT_CHANGED`.
5. `RootAnchor.verify()` queries the final handle again for reparse and identity; it must be callable at any time.

Why hold the whole chain? A handle to the project root with no-delete share blocks renaming the root, but does not block renaming `<workspace-parent>` (the parent). Holding every component with no-delete share blocks ancestor renames too. The cost is a handful of handles per session. It removes the "root moved after open" class rather than detecting it after the fact.

#### 4.5.2 Child directories

`DirectoryAnchor.open_child(name, create=False)`:

- desired access for a normal child: `FILE_TRAVERSE | FILE_READ_ATTRIBUTES | SYNCHRONIZE`; for a directory that will receive new children: add `FILE_ADD_FILE | FILE_ADD_SUBDIRECTORY`;
- share: `FILE_SHARE_READ | FILE_SHARE_WRITE`; **never `FILE_SHARE_DELETE`**;
- options: `FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT`;
- refuse any reparse attribute/tag;
- query identity; require the same volume serial;
- on any failure, close the just-opened handle; the parent remains owned by its caller.

`create=True` uses `FILE_OPEN_IF` only after `probe_absent`/journal intent; the no-replace property is still enforced by the create disposition. Do not use `FILE_OVERWRITE_IF` for a directory.

#### 4.5.3 File targets

`TargetAnchor.open_existing(parent, name, access)`:

- desired access: `GENERIC_READ | FILE_READ_ATTRIBUTES | SYNCHRONIZE`; add `FILE_WRITE_DATA` only if the operation will write through the handle; add `DELETE` only for rename/delete;
- share: `FILE_SHARE_READ`; **never `FILE_SHARE_WRITE`, never `FILE_SHARE_DELETE`** for a target that must not change under us;
- options: `FILE_NON_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT`;
- refuse directories, reparse points, and `nlink != 1`;
- query identity, size, attributes, and hash; the expected identity/hash must be supplied by the caller for replace/delete.

The target handle is held from admission to the final postcondition. For a replace, the handle's `DELETE` access is what permits the rename to quarantine; its share mode prevents other processes from writing or renaming the target while held.

### 4.6 Session state

```
SafeWriter.open()
  root = RootAnchor.open(path, expected=trusted_identity)
  root.verify()
  creation = root.open_child_dir("creation", create=False)
  lock = LockManager.acquire(creation, owner)
  operations = creation.open_child_dir("operations", create=True)
  quarantine_root = creation.open_child_dir("quarantine", create=True)
  journal = OperationJournal.open(operations, batch_id)
  session.anchors = {root, creation, operations, quarantine_root, journal, lock}
  session.verify_binding()   # all anchors, not just root/parent
```

If any step fails, close in reverse order. If `lock` is acquired and a later step fails, unlock and close before returning. Do not leave a stale owner record as a substitute for a released lock.

### 4.7 Binding revalidation

`verify_binding()` is a **session-level** check, not a target-level helper:

1. Reopen the root path from the volume root with the same no-reparse rules and compare the final identity to `root.identity` and to the trusted root identity.
2. Reopen `creation` by path from the fresh root and compare to `session.creation.identity`.
3. Reopen `operations` by path from `creation` and compare to `session.operations.identity`.
4. Reopen `quarantine` by path and compare.
5. Query the **held** journal handle identity and compare to the journal identity recorded at open; query the **held** lock handle identity and compare to the lock identity.
6. For the target parent, reopen by path from the fresh root and compare to the held parent identity.
7. For create: verify the target name is still absent through the held parent.
8. For replace/delete: verify the target handle identity/hash/`nlink` and the target name's identity through the held parent.
9. Close every temporary handle even on mismatch. A mismatch returns `E_BINDING_CHANGED` with the component and both identities; no side effect follows.

Why reopen by path if the handles are already held and no-delete? Three reasons:

- The path can be changed before the first handle is opened; revalidation catches a root that was swapped between session open and a later phase.
- A filter driver or a directory junction can make a path resolve differently even when handles are held; identity comparison catches it.
- It is an independent check against a bug in the handle-relative code. A test can mutate the path and observe a refusal.

The check is not a substitute for using handles. It is a detector; the write path itself must never re-resolve a path.

### 4.8 Public result model

```
OperationResult:
  status: SUCCEEDED | REFUSED | FAILED | RECOVERY_REQUIRED | COMMIT_UNCERTAIN
  code: stable machine code (E_*)
  message: human explanation
  operation_id: batch_id
  journal:
    path: repo-relative path or null
    last_durable_seq: integer or null
    terminal_event: string or null
    terminal_persisted: boolean
  target:
    relative_path, existed, identity_before, identity_after,
    size_before, size_after, sha256_before, sha256_after, nlink
  durability: CONTENT_FLUSHED | DIRECTORY_FLUSHED | UNVERIFIED | NOT_APPLICABLE
  durability_claim: NONE | PROCESS_CRASH_RECOVERABLE | POWER_LOSS_UNVERIFIED
  recovery: null or { classification, evidence, allowed_actions, applied, side_effect }
  retry: NONE | NEW_OPERATION | EXPLICIT_REAPPLY
  warnings: [ ... ]
```

The five status values are the **only** public status values. `OK` is not a status and must not be emitted. A legacy adapter that receives `OK` from old code maps it to `SUCCEEDED` at the boundary and records a deprecation warning; the adapter does not make `OK` part of the new contract.

#### 4.8.1 Status, error, journal, durability, and retry relationships

| Status | Required state | Journal terminal | Durability | Retry |
|---|---|---|---|---|
| `SUCCEEDED` | All content postconditions verified; target identity/hash/nlink; no unresolved temp/quarantine for this operation | `COMMITTED` or `RECOVERED_COMMITTED` durable; `terminal_persisted=true` | `CONTENT_FLUSHED` or `DIRECTORY_FLUSHED` | `NONE`; a new operation is a new intent |
| `REFUSED` | Precondition failed before any side effect; no content or metadata side effect | May have `SESSION_OPEN`; no success terminal | `NOT_APPLICABLE` | `NEW_OPERATION` after the caller fixes the condition |
| `FAILED` | A side effect may have occurred but was fully compensated; pre-state verified | `FAILED` durable, or `ROLLED_BACK` plus verification | `NOT_APPLICABLE` | `NEW_OPERATION`; never automatic |
| `RECOVERY_REQUIRED` | State is uncertain or a required terminal event is missing; content may be committed | `terminal_persisted=false` allowed; diagnosis owns the next step | `UNVERIFIED` unless the specific durability case says otherwise | `EXPLICIT_REAPPLY` only after diagnosis; no automatic retry |
| `COMMIT_UNCERTAIN` | The commit rename may or may not have happened; target may be `new` or `?`; no `COMMITTED` | `terminal_persisted=false` allowed | `UNVERIFIED` | `NONE` automatic; diagnose first; a blind retry is forbidden |

Rules:

- `SUCCEEDED` requires a durable success terminal event. If `COMMITTED` (or `RECOVERED_COMMITTED`) cannot be appended or flushed, the result is `RECOVERY_REQUIRED`, even if the bytes are visible and read back. A visible file is not a durable success record.
- `REFUSED` is not a synonym for "any error". It is reserved for a precondition failure with no side effect. An error after a temp was created is at least `FAILED` (if fully cleaned) or `RECOVERY_REQUIRED`.
- `FAILED` is not used when the pre-state or the compensation is uncertain. That is `RECOVERY_REQUIRED`.
- `COMMIT_UNCERTAIN` is a subtype of recovery-required for API purposes. It is kept distinct because the incident response differs: the target may be the intended new object, and a rollback would delete intended content.
- `journal.terminal_persisted=false` is incompatible with `SUCCEEDED`. The only exception is a read-only diagnosis result, which is not an operation success.
- `durability_claim=POWER_LOSS_UNVERIFIED` is mandatory unless `T-53` (disposable-VM power-loss) has passed for the exact build. A successful directory flush does **not** upgrade this claim.
- `retry=EXPLICIT_REAPPLY` means the caller may invoke the recovery CLI again after a new diagnosis. It does not authorize an in-process retry loop.

#### 4.8.2 `E_JOURNAL_APPEND` is state-dependent

`E_JOURNAL_APPEND` does not have one status. The mapping is:

| Where the append failed | Status | Side effect already? | Required behavior |
|---|---|---|---|
| Before any intent event and before any side effect | `REFUSED` | No | Close; no content action |
| After a durable intent but before the side effect it authorizes | `RECOVERY_REQUIRED` | No | The intent is durable; the side effect is **not** attempted; `rollback_attempted=false`; explicit diagnosis may reapply |
| After a side effect but before its completion record | `RECOVERY_REQUIRED` or `COMMIT_UNCERTAIN` | Yes | Preserve; diagnose; no automatic retry |
| After content success but before the terminal record | `RECOVERY_REQUIRED` | Yes | Content may be published; no `SUCCEEDED`; metadata adoption or diagnosis |

The per-operation tables in Section 7.2.3, Section 7.3.1, and Section 7.8 are normative; this table is the common rule.


---

---

## 5. Trust root and root identity

### 5.1 Why a path is not a trust root

A path is a name for a directory entry, not an object. Between `CreateFileW("<repo-root-escaped>")` and the next operation, another process can rename the directory, create a new one at the same name, or replace an ancestor with a junction. The 2.4 prototype correctly moved to handle-relative traversal, but it still opens the root path at session start and then compares the result to a caller-supplied identity. That is only as strong as the identity supply.

The trust root must answer two separate questions:

1. **Which object is the project root for this session?** Answer: a `(VolumeSerialNumber, FileId)` pair, not a string.
2. **Why does the user believe that pair is the project?** Answer: an explicit enrollment record, or a caller-supplied identity from a trusted context. "I opened the path and recorded what I found" is not an answer.

### 5.2 Root identity

```
RootIdentity:
  volume_serial_number: uint32
  file_id: 128-bit (FILE_ID_INFO.FileId)
  volume_guid_path: string   # diagnostic only; not the lookup key
  canonical_path: string     # diagnostic/lookup hint; never sufficient
```

- `FILE_ID_INFO` is returned by `GetFileInformationByHandleEx(..., FileIdInfo, ...)`; it contains the volume serial number and the 128-bit file identifier. See Section 12.
- The volume serial is also checked against `GetVolumeInformationW` for the volume root. A mismatch is `E_VOLUME_CHANGED`.
- File IDs are unique on a volume while the object exists. Do not assume they are never reused after deletion; the design never treats an ID alone as proof that a *deleted* object is the same object. It only uses the ID while a handle is held, or while correlating a journal record with a live object plus a path.
- The canonical path is used to locate the object for revalidation and for user messages. It is not an authorization token.

### 5.3 Trust record

Strict mode requires a trust record outside the project tree. The record is a **consistency and anti-accident control**, not a cryptographic attestation and not a security boundary against a malicious same-user process (B2). Its purpose is to prevent a path that was swapped before Veyra started from being silently accepted, and to make project moves and identity changes explicit.

#### 5.3.1 Storage, ownership, and boundary

Proposed location:

```
%LOCALAPPDATA%\Veyra\trusted-roots.json
```

- The directory and file are owned by the current user. The DACL is protected (inheritance disabled) and grants full control only to the owner, `SYSTEM`, and `Administrators`; no other principal receives an allow ACE. `Users`, `Authenticated Users`, `Everyone`, `INTERACTIVE`, and `ANONYMOUS LOGON` must not have write, delete, change-permissions, or take-ownership access. A read-only ACE for those principals is also disallowed for this file.
- The trust store is outside the project root. It must not live in the repository, a synced folder, a network share, or a reparse point.
- The record is not encrypted and contains no file content. It contains paths and file identities, which are treated as low-sensitivity local metadata.
- The record is not a defense against B2. A same-user attacker can edit the project directly and can edit this file; the design does not claim otherwise. The ACL prevents other low-privilege users and accidental profile copying from silently changing trust.

#### 5.3.2 Record schema, version 2

`trusted-roots.json` is UTF-8 without BOM, at most 1 MiB, and contains exactly one JSON object. Version 2 is the only version admitted by this design. Unknown top-level or entry fields are rejected; a future change must bump `schema_version` and define a migration.

```json
{
  "schema_version": 2,
  "record_id": "5f0c2f2e-6a0e-4a0e-9d5e-2f6a7b8c9d10",
  "revision": 1,
  "owner_sid": "S-1-5-21-1111111111-2222222222-3333333333-1001",
  "created_at": "2026-11-22T00:00:00Z",
  "updated_at": "2026-11-22T00:00:00Z",
  "roots": [
    {
      "path_key": "<repo-root-key>",
      "canonical_path": "<repo-root-escaped>",
      "identity_key": "499602d2:0123456789abcdef0123456789abcdef",
      "volume_serial_number": 1234567890,
      "volume_guid_path": "\\\\?\\Volume{00000000-0000-0000-0000-000000000000}\\",
      "file_id_hex": "0123456789abcdef0123456789abcdef",
      "filesystem": "NTFS",
      "owner_sid": "S-1-5-21-1111111111-2222222222-3333333333-1001",
      "enrolled_at": "2026-11-22T00:00:00Z",
      "last_verified_at": "2026-11-22T00:00:00Z",
      "tool_version": "0.x.y",
      "acl_fingerprint": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "entry_digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }
  ],
  "record_digest": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
}
```

Field rules:

- `schema_version` is the integer `2`. Any other value is `E_TRUST_SCHEMA_UNSUPPORTED`; there is no best-effort parse.
- `record_id` is a UUIDv4 generated at record creation. `revision` is a positive integer incremented on every successful enrollment or re-enrollment. It is an audit aid, not a monotonic security counter (see Section 5.3.5).
- `owner_sid` is the current user's SID in string form. Every entry repeats it so a copied or merged entry can be rejected without relying on the top-level field alone.
- `path_key` is `ntpath.normcase(canonical_path)` after the path has passed the same validation as the project root. It is a lookup key, never an authorization token.
- `canonical_path` is the display path. It must already be absolute, validated, and free of `.`/`..` components; the record loader does not normalize an untrusted path into validity.
- `identity_key` is the lowercase string `<volume_serial_number:08x>:<file_id_hex>`. It must be recomputed from the two identity fields; a mismatch is corruption.
- `volume_serial_number` is the 32-bit `FILE_ID_INFO.VolumeSerialNumber` of the volume that contains the root.
- `file_id_hex` is the 128-bit `FILE_ID_INFO.FileId`, lowercase, exactly 32 hex characters.
- `volume_guid_path` is the volume GUID path returned by `GetVolumeNameForVolumeMountPointW`. It is diagnostic and a mount-point-change hint; the lookup key is the path plus the identity pair, never the GUID string alone.
- `filesystem` must be `NTFS`.
- `enrolled_at` and `last_verified_at` are RFC 3339 UTC timestamps. `last_verified_at` is updated only after a successful identity verification; it is not updated by a mere file read.
- `tool_version` is the Veyra version that wrote the entry, for diagnosis only.
- `acl_fingerprint` is `sha256:` plus the SHA-256 of the canonical SDDL of the file's owner and DACL at enrollment. It detects accidental ACL drift; it is not a signature.
- `entry_digest` is `sha256:` plus the SHA-256 of the canonical JSON of the entry **excluding** `entry_digest` (`json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))`, UTF-8).
- `record_digest` is `sha256:` plus the SHA-256 of the canonical JSON of the object **excluding** `record_digest`, with each `entry_digest` included. This detects accidental corruption; a same-user attacker who can rewrite the file can also recompute it (B2).

#### 5.3.3 Load and verification sequence

`TrustStore.load()` runs this sequence before any project handle is opened:

1. Resolve `%LOCALAPPDATA%` through `SHGetKnownFolderPath(FOLDERID_LocalAppData)`. Reject a non-absolute, non-NTFS, or reparse path.
2. Open `Veyra` and `trusted-roots.json` through a no-reparse directory chain, with share `FILE_SHARE_READ` (no write, no delete) and `FILE_OPEN_REPARSE_POINT`. If either path is absent, return `E_TRUST_RECORD_MISSING`; do not create it implicitly. Reject a reparse point, a directory, or `nlink != 1` on the file.
3. Verify the owner SID is the current user SID for both the `Veyra` directory and the file. Enumerate both DACLs and reject any allow ACE for a non-owner principal other than `SYSTEM` or `Administrators`; reject any non-owner write/delete/change-permissions/take-ownership ACE. If either ACL is not owner-only, return `E_TRUST_ACL_UNSAFE`; do not read the record.
4. Read the file with a 1 MiB bound. Reject invalid UTF-8, a BOM, a non-object root, or trailing non-whitespace data.
5. Validate `schema_version`, every required field, every field type, `owner_sid`, `path_key`, `identity_key`, hex lengths, and duplicate `path_key`/`identity_key`. Recompute `entry_digest` and `record_digest`.
6. Compare the current ACL fingerprint to `acl_fingerprint` for each entry that will be used. A mismatch is `E_TRUST_ACL_CHANGED`; require explicit re-enrollment. Do not auto-update the fingerprint.
7. Look up `path_key`. Cases:
   - found, identity matches the live root: proceed;
   - found, identity differs: `E_ROOT_CHANGED`;
   - not found by path, but `identity_key` matches an entry at another path: `E_ROOT_MOVED`;
   - not found, identity not present: `E_ROOT_UNTRUSTED`;
   - found by path, but the path no longer resolves: `E_ROOT_MISSING`.
8. Recheck the trust file identity before and after reading. If it changed, return `E_TRUST_RECORD_CHANGED` and do not use the parsed data.

Any failure in steps 1-returns `REFUSED` before a project root is opened. There is no implicit enrollment, no "repair and continue", and no fallback to a caller-supplied path without an identity.

#### 5.3.4 Enrollment and explicit confirmation

Enrollment is two explicit steps, not a side effect of the first project write:

1. `veyra trust-root --inspect <path>` prints, without writing:
   - canonical path and `path_key`;
   - volume serial, volume GUID path, filesystem;
   - 128-bit file ID and `identity_key`;
   - current owner SID and a human-readable ACL summary;
   - whether an existing entry matches, differs, or is absent.
2. `veyra trust-root --enroll <path> --expect-volume-serial <n> --expect-file-id <hex> --confirm-enroll` opens the root with the same no-reparse algorithm, computes the identity, and requires the observed values to equal the two expected values exactly. A mismatch returns `REFUSED` with both values; it does not enroll the observed object.
3. If no entry exists, the command creates the record (or adds an entry) with a new `revision` and `entry_digest`.
4. If an entry exists with the same identity and path, the command refreshes `last_verified_at` only. It does not change identity fields.
5. If an entry exists with a different identity or path, the command requires `--replace-trust` and displays both old and new identities. Without that flag, it returns `E_TRUST_CONFLICT`.
6. If the record is corrupt, the command requires both `--replace-trust` and `--replace-corrupt`; it renames the old file to `trusted-roots.json.bak.<UTC>` in the same owner-only directory, then writes a new record. It never deletes the old file. Without both flags, it returns `E_TRUST_RECORD_CORRUPT`.
7. A non-interactive caller can supply the same expected values and confirmation token; a library call that omits them is `REFUSED`. Tests inject a `RootIdentity` directly and must never write the real profile record.

Enrollment writes through an anchored parent in `%LOCALAPPDATA%\Veyra`:

- create `trusted-roots.json.tmp.<random>` with `FILE_CREATE`, write, flush, read back, and verify the canonical JSON digest;
- rename the existing record to `trusted-roots.json.bak.<UTC>` with no-replace; flush the directory;
- rename the temp to `trusted-roots.json` with no-replace; flush the directory;
- if the process stops between the two renames, the target is absent and the loader returns `E_TRUST_RECORD_MISSING`; it does not auto-adopt the backup. The operator can restore the backup explicitly.

The temporary file and backup use the same ACL as the directory. They are not auto-deleted; stale `.tmp` or `.bak` files are ignored by the loader and reported for cleanup.

#### 5.3.5 Project moves, volume changes, recreation, copying, rollback, and tampering

| Event | Detection | Required behavior |
|---|---|---|
| Project moved within the same volume | Same `identity_key`, different `path_key` | `E_ROOT_MOVED`; do not auto-update; run `--inspect` and re-enroll explicitly. |
| Drive letter or mount point changed, same volume | `volume_guid_path` and identity are stable; path or GUID string differs | Treat as `E_ROOT_MOVED` unless the volume serial or file ID also differs; require re-enrollment. |
| Volume reformatted or replaced | Volume serial, volume GUID, or file ID differs; filesystem not NTFS | `E_VOLUME_CHANGED` or `E_ROOT_CHANGED`; no auto-update; no write. |
| Root deleted and recreated at the same path | Same `path_key`, different `identity_key` | `E_ROOT_CHANGED`; do not accept the new object. |
| Trust record copied to another user profile | `owner_sid` differs from the current user, or ACL is not owner-only | `E_TRUST_RECORD_FOREIGN`/`E_TRUST_ACL_UNSAFE`; refuse. |
| Trust record copied within the same profile | Same owner SID and ACL; record is valid | Accepted; copying is not a security event for the same user. |
| Valid older record restored (rollback) | `revision` is lower, but digests are valid | **Not reliably detectable.** If the restored identity matches the live root, it is accepted; if it differs, `E_ROOT_CHANGED`. `revision` is audit-only; there is no tamper-proof monotonic counter. |
| Record tampered with and digest recomputed | Digests validate | **Not detectable** by the record alone; this is B2 and outside the boundary. |
| Accidental corruption or partial write | JSON/schema/digest/duplicate validation fails | `E_TRUST_RECORD_CORRUPT`; no auto-repair; explicit `--replace-corrupt` plus backup. |
| ACL changed after enrollment | Current canonical SDDL fingerprint differs | `E_TRUST_ACL_CHANGED`; refuse until explicit re-enrollment. |

The design does not claim rollback detection or same-user tamper detection. Adding a DPAPI-protected key, a machine service, or an append-only external log would be a different design with a different threat model; it is not smuggled into 2.5R as an implied guarantee.

#### 5.3.6 What the trust record cannot do

- It cannot authenticate the very first enrollment. The user confirms what the path resolves to at that moment; if the path was already maliciously swapped, the record captures the attacker's object. This is a documented first-enrollment residual, not TOFU: Veyra never enrolls implicitly on first write.
- It cannot protect against B2. A same-user process can edit the project and, if it can write the profile, the record.
- It cannot detect a valid older record restored by the same user. The ACL is the boundary; rollback detection would require an external monotonic store.
- It cannot make a project multi-user safe. If another user has write access, that user is B2-equivalent for the objects they can modify.
- It cannot turn a path into an identity. The identity pair remains the authorization key; the record only says which pair the user confirmed.


### 5.4 First-session and test behavior

- Production CLI: no identity means no write. The user must enroll.
- Tests: inject `RootIdentity` from the temp directory's freshly opened handle. Never write a trust record into the repository or the user profile during tests.
- A future installer may offer enrollment as part of project creation, but it must still show the identity and require confirmation; it must not silently enroll on first `create`.

### 5.5 What this does not solve

The very first enrollment still trusts what the user sees at that moment. If the path was already maliciously swapped before the user ever enrolled, Veyra cannot know. The mitigation is user-visible identity confirmation and the requirement that the project root ACL is owner-only. This is a documented residual assumption, not a claim of absolute authenticity.

---

## 6. Windows semantics: what is documented, what was reproduced

This section is the factual foundation for the protocol. It deliberately records negative results. A design that assumes a Windows guarantee that does not exist is worse than a design that documents the gap.

### 6.1 File identity and link count

| Fact | Evidence | Consequence |
|---|---|---|
| `FILE_ID_INFO` returns `VolumeSerialNumber` plus a 128-bit `FileId`; `GetFileInformationByHandleEx(FileIdInfo)` is the supported query. | [DOC] `FILE_ID_INFO` and `GetFileInformationByHandleEx`; [CODE] `native.query_identity` uses it. | Identity comparisons are exact, not path-based. |
| `BY_HANDLE_FILE_INFORMATION` exposes `nNumberOfLinks`, size, attributes, and times. | [DOC] `GetFileInformationByHandle`; [CODE] `native.query_info`. | `nlink` can be checked at each handle-based checkpoint. |
| A file ID is unique on a volume while the file exists. | [DOC] `FILE_ID_INFO` remarks. | Do not use a file ID as a durable name after deletion; correlate with journal and path. |
| `nlink > 1` means another directory entry refers to the same file. | [DOC] `BY_HANDLE_FILE_INFORMATION`. | A target with `nlink > 1` may be modified through an unknown alias; refuse. |

### 6.2 Reparse points

| Fact | Evidence | Consequence |
|---|---|---|
| `FILE_OPEN_REPARSE_POINT` suppresses normal reparse processing and opens the reparse object itself. | [DOC] `NtCreateFile` create options; [EXP] a junction opened with this option had final path equal to the junction path, not the target. | The design can inspect a link without following it. |
| A junction created with `mklink /J` has `FILE_ATTRIBUTE_REPARSE_POINT` and tag `0xA0000003` (`IO_REPARSE_TAG_MOUNT_POINT`). | [EXP] `junction_attrs: attrs=1040, tag=0xA0000003`. | Rejecting the attribute is enough to refuse the junction; the tag is logged for diagnosis. |
| A symbolic link can be created only with `SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE` (Developer Mode) or sufficient privilege. | [DOC] `CreateSymbolicLinkW`; [EXP] `mklink /D` returned "You do not have sufficient privilege to perform this operation." | The current machine cannot prove symlink refusal. A Developer Mode or elevated VM test is required before admission. |
| A symlink and a junction are different reparse types; a junction test is not a symlink test. | [DOC]/[INF] | Section 9 has separate cases; do not mark symlink PASS from a junction result. |
| Microsoft's public documentation has open questions about combining `FILE_DIRECTORY_FILE` and `FILE_OPEN_REPARSE_POINT` on some builds. | [DOC] Q&A/issue references in Section 12; [EXP] the combination worked on Windows 11 26300. | Test the exact OS matrix; do not assume the combination is portable to older builds. |

**v1 decision:** reject **all** reparse points, including symlink, junction, mount point, and unknown tags. No follow, no `realpath`, no "but it points inside the root" exception. A path that traverses a reparse point is not the path the user thinks it is.

### 6.3 Share modes, deletion, and hardlinks

The prototype's report states that the root/parent rename was blocked by the directory handles. The experiment corrects that:

| Experiment | Result | Interpretation |
|---|---|---|
| Parent directory opened with `FILE_SHARE_READ \| FILE_SHARE_WRITE` (no delete); child file opened with `DELETE`, share `FILE_SHARE_READ`; rename child. | Success; final path updated. | A no-delete parent does **not** block a child's own rename. This is why C-01 can tighten directory shares without breaking commits. |
| Same parent; child directory opened with `DELETE`; rename child directory. | Success. | Same for directories. |
| Same parent; attempt to rename the parent itself. | `ERROR_SHARING_VIOLATION` (32). | A no-delete directory handle blocks renaming/deleting that directory. |
| Root/parent held with `FILE_SHARE_ALL`; a live descendant file with share `FILE_SHARE_READ` is open; rename root. | `PermissionError`/`WinError 5`. | The prototype's observed block came from the descendant file handle, not from the directory handle. The report's attribution at `docs/phase-3.2.2.1-2.4-safewriter-prototype-report.md:204` is wrong. |
| File opened with desired `GENERIC_READ \| GENERIC_WRITE \| DELETE`, share `FILE_SHARE_READ`; another process calls `CreateHardLinkW`. | Success; `nlink` becomes 2. | Share modes do **not** prevent hardlink creation. |
| Same, share `0` (no sharing at all). | Success; `nlink` becomes 2. | Even a fully exclusive data handle does not prevent a hardlink. |
| Same, file marked read-only. | Success; `nlink` becomes 2. | The read-only attribute does not prevent it. |
| Lock file opened with share `FILE_SHARE_READ`, byte-range lock held; rename/delete lock file. | Both fail with sharing violation 32. | The lock file name is stable while held, **provided** the parent does not grant delete. |
| Lock held; second handle in the same process opens read-only and tries to lock. | Lock fails with `ERROR_LOCK_VIOLATION` (33). | `LockFileEx` excludes same-process overlapping locks as well as cross-process. |
| Lock held; a second process opens read-only and tries to lock. | Lock fails with 33. | Cross-process exclusion works. |

**Consequences:**

1. Directory handles must deny delete (C-01). This blocks ancestor/root rename while the session is live. Holding the chain adds ancestor stability.
2. File handles must deny write and delete (share `FILE_SHARE_READ`) for a target that must not change. This blocks other writers, but not hardlink creation.
3. Hardlink defense cannot rely on share modes. It must use `nlink` checks at every checkpoint, and it must document the last-check-to-commit race (Section 11.1, X-01).
4. The lock file must be opened share `FILE_SHARE_READ | FILE_SHARE_WRITE` (no delete), not share `FILE_SHARE_READ` only, so a legitimate `release()` re-open can open with read/write while the lock is held. The lock byte, not the metadata, authorizes. The current `lock.py:101` share `FILE_SHARE_READ` works for a read-only second opener; if `release()` reopens with write, it must use a compatible share. The design standardizes on `READ | WRITE` for the lock file and verifies identity on release.

### 6.4 Rename semantics

| Fact | Evidence | Consequence |
|---|---|---|
| `NtSetInformationFile(FileRenameInformationEx)` can rename a file relative to a directory handle. | [DOC]/[CODE] `native.rename`; [EXP] target final path changed under the same parent. | The commit never needs a path string. |
| `FILE_RENAME_INFORMATION_EX` flags = 0 means no replace; an existing destination yields `STATUS_OBJECT_NAME_COLLISION` (`0xC0000035`). | [EXP] `no_replace_collision -> STATUS_OBJECT_NAME_COLLISION`; [DOC] `FILE_RENAME_INFO`. | Create and replace commit use no-replace; a collision is a refusal, never an overwrite. |
| `ReplaceIfExists` (flag `0x1`) replaces the destination. | [EXP] `replace_collision -> STATUS_SUCCESS`; [DOC]. | Used only by an explicit, already-authorized replace of the exact target, never by create and never by recovery. |
| A rename can move a file to a different directory on the same volume using the destination directory handle. | [EXP] `cross_dir_rename -> STATUS_SUCCESS`, target file appeared in the other directory. | Temp staging can live under `creation/operations/tmp/` and commit into any business parent on the same volume. |
| Renaming an open file requires a handle with `DELETE` access; the source handle's share mode must be compatible with the destination open. | [DOC]/[CODE]/[EXP]. | `_TEMP_ACCESS` and the replace target handle include `DELETE`; other openers are blocked by share `READ`. |

### 6.5 Locking

`LockFileEx` locks a byte range on a file handle. It is mandatory only among processes that call it; it is not a filesystem ACL and does not stop a process that ignores it.

- The lock region is one byte at offset 0, exclusive, immediate (`LOCKFILE_FAIL_IMMEDIATELY`). No blocking wait in the default path.
- The lock file is opened through the anchored `creation` directory, not by path.
- The lock file is opened with `FILE_OPEN_IF`, desired `GENERIC_READ | GENERIC_WRITE | FILE_READ_ATTRIBUTES | SYNCHRONIZE`, share `FILE_SHARE_READ | FILE_SHARE_WRITE`, no delete.
- Before locking: reject reparse, `nlink != 1`, wrong identity. After locking: write the owner metadata (token, pid, batch_id, root identity, host, tool version, UTC time) and flush. The metadata is diagnostic; the lock byte is authoritative.
- `release()` uses the **held** handle: unlock, close. It must not reopen by path. If the process cannot unlock (rare), close still releases the OS lock; report a warning.
- A stale lock file from a crashed process is harmless: no process holds the byte, so the next `LockFileEx` succeeds. The owner metadata is overwritten only after the new lock is acquired.
- A second process that opens the lock file with write access while the first holds it will fail at open with sharing violation (32) if the first's share excludes write; with the design's share `READ|WRITE`, it can open, but its `LockFileEx` fails with 33. Both are `E_LOCK_BUSY`; the message must distinguish open-vs-lock only for diagnosis.
- Deleting or renaming the lock file while held fails with 32 (observed). With the no-delete `creation` directory handle, the parent cannot be renamed either. This closes the "new lock file at a new creation directory" hole only if `creation` identity is also revalidated (C-03).

### 6.6 Directory flush

`FlushFileBuffers` is documented for file handles. There is no public guarantee that calling it on a directory handle makes directory-entry changes durable across power loss. On this machine, a directory handle opened with `GENERIC_READ | GENERIC_WRITE` and `FILE_FLAG_BACKUP_SEMANTICS` accepted `FlushFileBuffers` and returned success.

Therefore:

- Call `FlushFileBuffers` on the file after writing and on the relevant parent directory after create/rename/delete.
- Treat a flush failure as `E_DURABILITY_UNCERTAIN` and report `UNVERIFIED`; do not claim success.
- Treat a flush success as "the request was accepted", not as a power-loss guarantee.
- The only way to admit a durability claim is a power-loss test on a disposable VM with the actual NTFS build, repeated at each transition. That test is not run here (Section 9.6).

### 6.7 Crash consistency versus durability

These are separate:

- **Crash consistency:** after a process kill, the journal and filesystem can be classified and recovered without silent data loss.
- **Durability:** after a power loss, the bytes and directory entries are still there.

This design can aim for crash consistency with a journal and handle-relative operations. It cannot prove durability from `FlushFileBuffers` alone. The public API must not say "safe against power loss"; it may say "journaled; recovery required if the process or machine stops".

#### 6.7.1 Durability failure classes

The aggregate `E_DURABILITY_UNCERTAIN` is split because the four failures have different truth conditions. A single "durability warning" must not hide a missing content postcondition.

| ID | Failure | Content postcondition verified? | `SUCCEEDED` permitted? | Required status / code | Durability claim |
|---|---|---|---|---|---|
| DUR-1 | Content-file `FlushFileBuffers` failed before commit | No (target not published) | No | `FAILED` `E_DURABILITY_CONTENT_FLUSH`; target unchanged | `NONE` |
| DUR-2 | Content-file flush failed after target publication | Identity/hash read-back may pass, but the bytes are not proven durable | **No** | `RECOVERY_REQUIRED` `E_DURABILITY_CONTENT_FLUSH`; preserve target; diagnose | `UNVERIFIED` |
| DUR-3 | Directory-entry flush failed after a verified commit | Yes | Yes, with warning | `SUCCEEDED`, `durability=DIRECTORY_FLUSHED`, warning `W_DURABILITY_DIRECTORY` | `POWER_LOSS_UNVERIFIED` |
| DUR-4 | Journal terminal-event flush failed, or journal verification failed | Content may be verified | **No** | `RECOVERY_REQUIRED` `E_DURABILITY_JOURNAL_FLUSH` or `E_DURABILITY_JOURNAL_CORRUPT`; `journal_terminal=false` | `UNVERIFIED` |

Rules:

- DUR-1 is a content failure, not a durability footnote: the target is not published, so the operation did not succeed.
- DUR-2 is not `SUCCEEDED` even if a read-back sees the new bytes. The bytes may be lost on power failure, and the journal cannot prove otherwise.
- DUR-3 is the only case where the content is published and verified but a directory entry is not proven durable. The result may be `SUCCEEDED` with `durability=DIRECTORY_FLUSHED`; the public wording must say "committed; directory-entry durability unverified", never "power-loss safe".
- DUR-4 is a recovery case. A missing terminal record is not a warning that can be attached to success.
- `E_DURABILITY_UNCERTAIN` is retained in the glossary only as a deprecated aggregate for old callers; new code emits the four specific codes and must not use the aggregate to describe a success.


### 6.8 Symlink creation and test environments

- `CreateSymbolicLinkW` with `SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE` requires Developer Mode; without it, the caller needs `SeCreateSymbolicLinkPrivilege` (typically elevated).
- The current account is non-elevated and Developer Mode is not enabled; `mklink /D` failed. This is an environment limitation, not evidence that symlink refusal works.
- A junction (`mklink /J`) does not require that privilege and was used to prove the no-follow open path.
- Admission requires at least one Windows environment where a real symlink can be created, and a test that the symlink is refused without following. Until then, the symlink row in Section 9 is `UNVERIFIED`, not PASS.

### 6.9 Names, Unicode, ADS, and long paths

- NTFS names are UTF-16. Python `str` can contain unpaired surrogates; `name.encode("utf-16-le")` raises `UnicodeEncodeError: surrogates not allowed` in `native._us` (`native.py:266`). The prototype lets this escape in some paths; production must reject it in `policy.validate_simple_name` before any native call.
- Reject `:` in a simple name. It is an alternate data stream separator (`file.txt:stream`), not part of the file name. Also reject `/`, `\`, NUL, control characters `0x00-0x1F`, and the reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, including extensions such as `CON.txt`).
- Reject trailing dot or space; Win32 normalization can otherwise map two names to one object.
- Reject `.` and `..` as components. The prototype's `validate_root_path` calls `ntpath.normpath` first (`policy.py:135`) and then checks components (`:147`), so `C:\foo\..\bar` becomes `C:\bar` and passes. That contradicts its docstring and is a path-confusion bug. Validate raw components before normalization; normalization may only be used for display after validation.
- Use `\\?\` extended-length paths for the root if needed. Handle-relative native opens avoid the 260-character limit for descendants, but the root path itself and diagnostics still need a defined limit. Reject paths above the supported limit with `E_PATH_TOO_LONG`.
- Case: NTFS is case-insensitive by default. `create("Readme.md")` when `README.md` exists must be a collision, not a new file. The no-replace rename and `probe_absent` handle this at the object level; the API must not promise case-sensitive semantics.
- Unicode normalization: do not normalize names. NTFS stores the exact UTF-16 sequence; normalization could target a different object. Reject invalid sequences; otherwise pass through.

### 6.10 ctypes ABI and error mapping

- Use `ctypes.WinDLL("kernel32", use_last_error=True)` and `ctypes.WinDLL("ntdll")`; declare `argtypes`/`restype` for every function. `HANDLE` is pointer-sized; never store it in a 32-bit int. On x64 it is 64-bit.
- `NTSTATUS` is a signed 32-bit value; negative values are errors. Compare via `status < 0` and the unsigned `0xFFFFFFFF` mask for names. Do not rely on `GetLastError` after an NT call; use the returned status and `RtlNtStatusToDosError`.
- Structures passed to the kernel must have explicit `_fields_`, correct alignment, and a size checked at import. `FILE_RENAME_INFORMATION_EX` and `FILE_ID_INFO` are version-sensitive; add a startup self-test that queries a known file and asserts the structure size/round-trip.
- `UNICODE_STRING.Length`/`MaximumLength` are byte counts; `_us` already computes UTF-16 code units. Keep that; add a test with a non-BMP character and a surrogate.
- Convert every native failure to a typed `SafeWriterError` with a stable code, operation, relative path, and status. Do not expose raw `OSError` from the public API.
- Run on 64-bit Python only for admission; 32-bit is out of scope. Test on Windows 10 and 11 builds listed in Section 9.

### 6.11 Antivirus, EDR, and filter drivers

- A scanner may open the file with a share mode that conflicts with the target's share. The operation must fail closed with `E_SHARING_VIOLATION`, not retry indefinitely.
- A scanner may hold the target open; the target handle share `READ` still allows read-only scans. A scanner that requests write/delete will be blocked; that is correct for integrity.
- Do not add a blanket retry loop around rename/delete. A bounded retry (for example, 3 attempts over 100 ms) may be allowed only for a **read-only probe** and must be documented. A retry around a side effect can duplicate an operation.
- EDR can inject a DLL or delay a call; this is outside the model. Report the failure, preserve the scene, and let recovery diagnose.

---

## 7. Operation protocols

### 7.1 Common session protocol

Every operation starts with the same sequence:

1. Validate the public request:
   - root path, relative target path, expected identities, operation type, optional caller token;
   - reject unknown fields? The public API should reject unknown arguments at the Python boundary, not silently ignore them.
2. Load the trusted root identity:
   - caller-supplied, or the trust record;
   - no implicit enrollment.
3. Open the root chain (Section 4.5.1). On any failure, close all handles.
4. Open `creation` (must exist; do not create it implicitly for a write; `ProjectStore.initialize` owns creation).
5. Acquire the lock (Section 6.5). On failure, close `creation`/root.
6. Open `operations` and `quarantine`; open/create the journal for `batch_id`.
7. `verify_binding()` for all anchors.
8. Append `SESSION_OPEN` with root identity, operation, target relative path, expected target identity (or "absent"), tool version, and UTC time; flush.
9. Run the operation-specific protocol.
10. On every exit path: append a terminal event if the journal is writable; flush; close journal; close quarantine/operations; release lock; close creation; close root chain. If a close/flush fails, report `RECOVERY_REQUIRED` even if the content operation appeared to succeed.

`batch_id` is generated once per public operation, cryptographically random (for example, 128-bit hex). It is not reused for a retry. A retry is a new operation with a new journal; recovery correlates via the target path and identities, not by reusing the ID.

### 7.2 Create

Preconditions:

- target relative path passes `validate_relpath`;
- target name is absent at admission (through the held parent);
- parent directories exist or are creatable;
- no reparse point in the path;
- no protected file.

#### 7.2.1 Create transition table

This table is the normative Create state machine. "Durable event" means the event was appended and flushed before the side effect in the next row. A row that names no side effect is a checkpoint, not a permission.

| Step | Durable event | Precondition | Side effect | Postcondition | Failure class |
|---|---|---|---|---|---|
| C0 | `SESSION_OPEN` | root/lock/binding pass | none | all anchors bound | `REFUSED` |
| C1 | `PARENT_DIRS_INTENT` | list of missing components; each parent identity and intended name recorded | none | intent contains every directory this operation may create | `REFUSED`/`FAILED` |
| C2 | none (repeat C3/C4 per directory) | the directory is the next missing component; its parent is bound | create one directory with `FILE_OPEN_IF`; never replace an existing non-directory | new directory exists; identity queryable | `FAILED`/`RECOVERY_REQUIRED` |
| C3 | `PARENT_DIR_CREATED` | directory created by C2; identity observed | none | identity/name/parent recorded for cleanup | `RECOVERY_REQUIRED` if append fails |
| C4 | `PARENT_READY` | all missing parents now exist; identities recorded | none | parent chain verified | `RECOVERY_REQUIRED` if append fails |
| C5 | `TARGET_ABSENT` | target absent through held parent | none | absence evidence recorded | `REFUSED` |
| C6 | `CREATE_INTENT` | parent identity; target absent; temp policy | none | intent durable before any temp | `REFUSED`/`FAILED` |
| C7 | `TEMP_READY` | temp created/written/flushed/read back; nlink==1 | create temp, write bytes, flush, read back (the temp side effect occurs **after** `CREATE_INTENT`) | temp identity/hash/size/nlink recorded | `FAILED`; `RECOVERY_REQUIRED` if append fails |
| C8 | `COMMIT_INTENT` | temp `new`; nlink==1; target absent; binding | none | intent durable before the rename | `RECOVERY_REQUIRED` if append fails |
| C9 | none | target absent; temp `new`; binding | rename temp -> target via held parent, replace=false | target `new` | `REFUSED`/`COMMIT_UNCERTAIN` |
| C10 | `COMMITTED` | target `new`; identity/hash/nlink verified; parent flush attempted | none | terminal success evidence | `RECOVERY_REQUIRED` if append fails |
| C11 | terminal/close | target `new` | close handles | journal closed | `RECOVERY_REQUIRED` if close/flush fails |

Rules that the table makes explicit:

1. **No temp before `CREATE_INTENT`.** C7's side effect is authorized only by C6. An implementation that creates a temp "to see if the path works" before the intent is non-conforming.
2. **Parent creation is not one atomic side effect.** Each directory is created and recorded separately. A crash between C2 and C3 leaves a directory that exists but has no identity record; recovery must not delete it (Section 7.2.5).
3. **The rename is the only content publication.** A success terminal requires target identity == temp identity, hash match, nlink==1, and a durable `COMMITTED`.
4. **A create never overwrites.** The rename uses replace=false. A collision is `REFUSED E_TARGET_EXISTS`; it is not retried with replace=true.
5. **A visible target is not a success.** If `COMMITTED` cannot be appended, the result is `RECOVERY_REQUIRED`; metadata adoption may close it without content change (Section 7.2.4).

#### 7.2.2 Create recovery state machine

This table is normative for Create. It covers the ten review cases plus the append-failure and binding cases. `new` is the temp identity/hash at `TEMP_READY`; `old` is not used by Create; `?` is an object that is not `new`. Absence is verified through the held parent, not by a path existence check.

| ID | Last durable event | Expected objects and identities | Required checks | Mode | Allowed side effect and prior durable event | Result / code | Final classification | If a precondition cannot be verified |
|---|---|---|---|---|---|---|---|---|
| CR-1 | `PARENT_DIRS_INTENT` or `PARENT_DIR_CREATED`; `PARENT_READY` absent | target absent; temp absent; zero or more created directories; only `PARENT_DIR_CREATED` identities are owned | each owned dir: identity matches, empty, parent bound, inside the target parent chain, created by this batch; unrecorded dirs are **not** owned | diagnose read-only; apply may clean one directory | `rmdir` one owned empty directory after `RECOVERY_INTENT` action `CLEAN_PARENT_DIRS`; postcondition directory absent | `FAILED` with `created_dirs_retained` if cleanup is refused; otherwise `FAILED` after cleanup | `CREATE_PARENT_PARTIAL` | Do not delete; preserve and report `E_PARENT_CLEANUP_REFUSED`; no recursive delete |
| CR-2 | `PARENT_READY` | target absent; temp absent; parents recorded | parent identities match; target absent; binding | read-only | none | `FAILED` with `target_created=false`; no content | `CREATE_NOT_STARTED` | No action; report the state |
| CR-3 | `TEMP_READY`; no `COMMIT_INTENT` | target absent; temp `new`, nlink==1 | temp identity == `new`; hash/size match; target absent; no `COMMIT_INTENT`; binding | diagnose read-only; apply may delete one temp | delete temp through its handle after `RECOVERY_INTENT` action `DELETE_OWNED_TEMP`; postcondition temp absent | `FAILED` after cleanup; `RECOVERY_REQUIRED` if delete fails | `ORPHAN_TEMP` | If temp is `?`, nlink>1, or target appears: no delete; `RECOVERY_REQUIRED` |
| CR-4 | `COMMIT_INTENT` | target absent; temp `new`, nlink==1 | temp identity/hash; target absent; binding; no `COMMITTED` | **diagnose read-only by default**; apply may choose exactly one action | (a) `RESUME_CREATE`: rename temp -> target, replace=false, after `RECOVERY_INTENT` action `RESUME_CREATE`; (b) `DISCARD_TEMP`: delete temp after `RECOVERY_INTENT` action `DISCARD_TEMP` | (a) `SUCCEEDED` with `recovery.applied=true` and `RECOVERED_COMMITTED` durable; (b) `FAILED` after temp delete | (a) `RECOVERED_COMMITTED`; (b) `ORPHAN_TEMP` | No automatic rename/delete. If target appears: reclassify CR-5/CR-6/CR-7. If binding fails: `RECOVERY_REFUSED`; no action |
| CR-5 | `COMMIT_INTENT`; target `new`; `COMMITTED` absent | target `new`, nlink==1; temp absent | target identity/hash == temp recorded values; nlink==1; parent bound; no `COMMITTED` | metadata-only apply | append `RECOVERED_COMMITTED` with target identity/hash/nlink; **no content change** | `SUCCEEDED` only if the record is durable; otherwise `RECOVERY_REQUIRED` `journal_terminal=false` | `COMMITTED_WITHOUT_RECORD` | If target hash/identity differs: `CREATE_TARGET_UNKNOWN`, no adoption |
| CR-6 | any create event through `COMMITTED` | target exists but identity != `new`; temp may be absent or `new` | target identity; hash; nlink; expected role; binding | read-only | none | `RECOVERY_REQUIRED` `E_TARGET_UNKNOWN` | `CREATE_TARGET_UNKNOWN` | Preserve the target; never overwrite, rename over, or delete it |
| CR-7 | any create event | temp and target both exist; identities may be `new`, `new` with nlink>1, or `?` | both identities; nlink; hashes; binding; whether the target hash equals the temp hash | read-only | none | `RECOVERY_REQUIRED`; if both are the same identity and nlink>1, `HARDLINK` | `CREATE_DUPLICATE_OBJECTS` or `HARDLINK` | Preserve both; no cleanup of either object |
| CR-8 | `COMMIT_INTENT` | target absent; temp absent | absence both through held parent; journal says `COMMIT_INTENT`; binding | read-only | none | `RECOVERY_REQUIRED` `E_CREATE_TEMP_LOST` | `CREATE_TEMP_LOST` | Incident; do not invent a replacement temp or a new target |
| CR-9 | any transition | depends on the failure row in Section 7.2.3 | Section 7.2.3 | Section 7.2.3 | Section 7.2.3 | Section 7.2.3 | Section 7.2.3 | Fail closed; no best-effort completion |
| CR-10 | any event | binding component changed during recovery | root/all internal anchors/parent/target/temp | read-only | none | `RECOVERY_REFUSED` `E_BINDING_CHANGED` | `RECOVERY_REFUSED` | If the change happened after a side effect, preserve and diagnose; do not repeat the side effect |

**Resolution of `COMMIT_INTENT + target absent + temp intact`.** This state is **not** proof that the rename failed. It is also not proof that the rename can be safely repeated. The default is read-only diagnosis (`CREATE_COMMIT_NOT_OBSERVED`). An explicit apply may choose exactly one of two auditable actions:

1. `RESUME_CREATE`: after a new `RECOVERY_INTENT` with the temp identity and expected target name, rename temp -> target with replace=false, verify target identity/hash/nlink, then append `RECOVERED_COMMITTED` and `RECOVERY_APPLIED`.
2. `DISCARD_TEMP`: after a new `RECOVERY_INTENT`, delete the owned temp through its handle, then append `RECOVERY_APPLIED`.

Both actions require the temp identity/hash to match the `TEMP_READY` record, nlink==1, target absent through the held parent, no `COMMITTED`, and binding. A second recovery run after either action must see the resulting state and perform no second rename or delete. An implementation must not choose one of these actions automatically.

#### 7.2.3 Create append and flush failure handling

The common invariant applies: no content side effect may occur unless its required intent event is durable. A flush failure is a durability failure, not a successful append.

| Transition | Observed after the failure | Status | Side effect already? | Required classification / behavior |
|---|---|---|---|---|
| `PARENT_DIRS_INTENT` append fails | no directory created | `REFUSED` | No | No cleanup needed; close |
| After a parent directory is created, before `PARENT_DIR_CREATED` is durable | directory may exist; identity not recorded | `RECOVERY_REQUIRED` | Yes (directory) | `CREATE_PARENT_PARTIAL`; no cleanup of the unrecorded directory; preserve and report |
| `PARENT_READY` append fails | created dirs recorded; no target/temp | `RECOVERY_REQUIRED` | Yes (directories) | `CREATE_PARENT_PARTIAL`; cleanup only recorded empty dirs |
| `TARGET_ABSENT` append fails | no target; parents recorded | `FAILED` | Yes (parents) | `CREATE_NOT_STARTED`; cleanup only recorded empty dirs; do not create a target |
| `CREATE_INTENT` append fails | no temp | `REFUSED`/`FAILED` | No (or parents) | `REFUSED` if no parent side effect; otherwise `FAILED` with cleanup |
| Temp write/flush fails before `TEMP_READY` is durable | temp may exist; identity not recorded | `RECOVERY_REQUIRED` | Yes (temp) | `CREATE_TEMP_UNRECORDED`; preserve; do not delete an unproven temp |
| `TEMP_READY` append fails | temp exists and is recorded only in memory | `RECOVERY_REQUIRED` | Yes (temp) | `CREATE_TEMP_UNRECORDED`; no automatic delete; diagnose |
| `COMMIT_INTENT` append fails | temp `new`; target absent; no rename | `RECOVERY_REQUIRED`; `journal_terminal=false` | Yes (temp) | `ORPHAN_TEMP` is the safe classification; delete only through explicit Section 7.2.2 CR-3 after the journal is writable; no automatic cleanup |
| Rename succeeds, `COMMITTED` append fails | target `new`; temp absent | `RECOVERY_REQUIRED` or `COMMIT_UNCERTAIN` | Yes (target) | `COMMITTED_WITHOUT_RECORD` if target verifies; no `SUCCEEDED`; metadata adoption only |
| Terminal close/flush fails | target `new`; journal may be closed | `RECOVERY_REQUIRED` | Yes | `COMMITTED_WITHOUT_RECORD` or `UNKNOWN_JOURNAL_STATE`; no content rewrite |

If the journal is corrupt or full at any row, the operation stops before the next side effect. A full journal may leave a temp; it does not authorize deleting that temp without a readable intent record.

#### 7.2.4 Create metadata adoption

A Create operation may have published the target but failed to append `COMMITTED`. Metadata adoption closes the record without changing content:

- Preconditions: target identity/hash equals the `TEMP_READY` values; target nlink==1; temp absent; no `COMMITTED`; parent binding verified; journal writable.
- Action: append `RECOVERED_COMMITTED` with target identity, hash, size, nlink, and `adopted_from=TEMP_READY`; flush.
- Result: `SUCCEEDED` with `recovery.applied=true` and `recovery.side_effect=none` only if the record is durable. Otherwise `RECOVERY_REQUIRED` with `journal_terminal=false`.
- Do not adopt if the target hash differs, nlink>1, target is `?`, temp also exists, or the binding fails. Those are CR-6/CR-7/CR-10.
- A second run that sees `RECOVERED_COMMITTED` is a no-op. It must not append a second adoption record.

#### 7.2.5 Parent-directory cleanup

Cleanup is allowed only when **all** of these hold:

1. The directory has a `PARENT_DIR_CREATED` record from this batch, with the identity observed immediately after creation.
2. The directory is still the same identity, is empty, and is inside the target parent chain.
3. The target is absent and no temp or quarantine object for this operation exists.
4. `verify_binding()` passes for the root, the directory's parent, `creation`, `operations`, and the journal.
5. The lock is held and the journal is writable.

Protocol:

1. Append `RECOVERY_INTENT` with action `CLEAN_PARENT_DIRS`, the directory identity, its parent identity, and the expected emptiness; flush.
2. Re-check identity and emptiness through the held parent.
3. Remove the directory with a handle-relative, non-recursive operation; never use a path-based `rmtree`.
4. Verify the name is absent; append `RECOVERY_APPLIED`; flush.
5. Process one directory per apply invocation, deepest first. A crash after the removal but before the record is classified `ALREADY_CLEANED`; the next run records metadata and performs no second removal.

If the directory is not empty, its identity changed, or an unrecorded directory is present, leave it and report `created_dirs_retained`. A directory created before `PARENT_DIR_CREATED` was durable has no ownership record and is never removed automatically. The operation may report `E_PARENT_CLEANUP_REFUSED`; it must not use recursion, a wildcard, or a path-only check.


### 7.3 Replace

Preconditions:

- target exists and is a regular file;
- expected identity/hash (or an explicit "accept current" flag that still records the observed identity/hash in the journal);
- `nlink == 1`;
- target parent and quarantine parent are anchored;
- target is not protected.

State machine:

| Step | Journal event | Side effect | Checkpoint / failure |
|---|---|---|---|
| 0 | `SESSION_OPEN` | none | binding |
| 1 | `TARGET_OPENED` with before identity, hash, size, nlink | open target by name through held parent, share READ, DELETE | reparse/dir/nlink/hash mismatch: `REFUSED` |
| 2 | `QUARANTINE_DIR_READY` with identity | create `creation/quarantine/<batch_id>` (no replace) | collision: retry with new batch? no; `REFUSED E_QUARANTINE_EXISTS` |
| 3 | `TEMP_READY` with temp identity/hash/nlink | create/write/verify temp | failure: cleanup temp; target unchanged; `FAILED` |
| 4 | none | `target.verify_unchanged()`; `verify_binding()` | mismatch: `REFUSED E_TARGET_CHANGED`; cleanup temp |
| 5 | `QUARANTINE_INTENT` with target identity, quarantine identity/name | none | binding |
| 6 | none | rename target -> quarantine via target handle, replace=false | collision: another object at quarantine name; `FAILED`, target unchanged; cleanup temp |
| 7 | `QUARANTINED` with quarantine identity == before identity, target now absent | none | query quarantine facts; target path absent through parent; binding |
| 8 | `COMMIT_INTENT` with temp identity, target name, quarantine identity | none | temp nlink==1; target absent; binding |
| 9 | none | rename temp -> target via held parent, replace=false | collision: target appeared; go to compensation; other error: classify |
| 10 | `COMMITTED` with target identity == temp identity, quarantine identity == old | none | open target by name, identity/hash/nlink; binding; flush parent |
| 11 | terminal | close temp; close target | `SUCCEEDED` only if all checks pass |

#### 7.3.1 Compensation

A `replace` can fail after the old target has moved to quarantine. The result must not be a bare `RECOVERY_REQUIRED` if a safe restoration is possible. The governing invariant is:

> **No content side effect may occur unless its required intent event was durably appended first.**

For rollback, the required intent is `ROLLBACK_INTENT`. If that append did not durably complete, **rollback was not attempted**; the operation must not claim otherwise, and it must not rename the quarantine object. This is the correction to the earlier text that described a rollback attempt and mapped `E_JOURNAL_APPEND` unconditionally to `REFUSED`.

Compensation is a recovery action, not an in-operation retry. It is allowed only when **all** of these are true:

1. The operation still holds the target parent, quarantine parent, quarantine file handle, and lock.
2. The target path is absent, verified through the held parent at compensation time.
3. The quarantine entry's identity equals the before identity, its hash equals the before hash, and `nlink == 1`.
4. No `COMMITTED` journal record for this operation exists.
5. `verify_binding()` passes for root, creation, operations, quarantine, lock, and parent.

Compensation protocol:

1. Append `ROLLBACK_INTENT` with target path, quarantine identity, expected absence; flush and verify the append.
2. Only after the intent is durable, rename quarantine -> target via the target parent, replace=false.
3. Open target by name; verify identity/hash == before; `nlink == 1`; verify binding.
4. Append `ROLLED_BACK`; flush; return `FAILED` with `restored=true` and `rollback_attempted=true`.
5. If any step fails, return `RECOVERY_REQUIRED` with the exact observed state. Do not delete the quarantine, do not overwrite an object at the target, do not try a second path.

#### 7.3.1.1 Compensation failure cases

| ID | Failure | Durable state | Content side effect permitted? | Required result | Recovery classification | Retry |
|---|---|---|---|---|---|---|
| C1 | `ROLLBACK_INTENT` append fails | target absent; Q `old`; temp `new`; no rollback intent | **No.** Do not rename; `rollback_attempted=false` | `RECOVERY_REQUIRED` with `E_JOURNAL_APPEND`; `rollback_intent_durable=false` | `ROLLBACK_NOT_AUTHORIZED` | No automatic; explicit diagnosis may retry the intent append, never the rename first |
| C2 | `ROLLBACK_INTENT` succeeds, rollback rename fails | target absent; Q `old`; temp `new`; intent durable | The rename was attempted and failed. No second content action | `RECOVERY_REQUIRED` with `E_ROLLBACK_FAILED`; `rollback_attempted=true`; preserve Q | `ROLLBACK_RENAME_FAILED` | No automatic; explicit reapply after diagnosis may retry the same durable intent |
| C3 | rollback rename succeeds, `ROLLED_BACK` append fails | target `old`; Q absent; no terminal record | **No further content change.** The old content is restored | `RECOVERY_REQUIRED` with `journal_terminal=false`; `rollback_attempted=true` | `ROLLED_BACK_WITHOUT_RECORD` | Next diagnosis appends `ROLLED_BACK`; no second rename |
| C4a | journal becomes corrupt/full before `ROLLBACK_INTENT` is durable | target absent; Q `old`; temp `new` | **No.** No intent, no rollback | `RECOVERY_REQUIRED` with `E_JOURNAL_CORRUPT` or `E_JOURNAL_FULL`; `rollback_attempted=false` | `JOURNAL_BLOCKED_BEFORE_ROLLBACK` | No automatic; repair/archive is a separate explicit action |
| C4b | journal becomes corrupt/full after `ROLLBACK_INTENT`, before rename | target absent; Q `old`; intent durable | **No new content action.** The terminal record cannot be guaranteed | `RECOVERY_REQUIRED`; preserve; `rollback_attempted=false` at this point | `JOURNAL_BLOCKED_AFTER_INTENT` | No automatic; explicit diagnosis only |
| C4c | journal becomes corrupt/full after rollback rename | target `old`; Q absent; no terminal | **No further content change.** Preserve restored target | `RECOVERY_REQUIRED`; `journal_terminal=false`; `rollback_attempted=true` | `ROLLED_BACK_WITHOUT_RECORD` | Metadata append only after journal repair |
| C5a | binding changes before compensation | target absent; Q `old`; temp `new` | **No.** The scene is not the session's scene | `RECOVERY_REFUSED` with `E_BINDING_CHANGED` | `RECOVERY_REFUSED` | No automatic; stop |
| C5b | binding changes during compensation, after the rename | target may be `old`; Q absent | **No repeat.** Preserve the observed state | `RECOVERY_REQUIRED`; report the observed identities | `ROLLBACK_BINDING_CHANGED` | No automatic; stop |
| C6 | `COMMITTED` already exists | target `new`; Q `old`; commit record durable | **No rollback.** `COMMITTED` wins | `SUCCEEDED` if target/Q verify; otherwise `RECOVERY_REQUIRED` | `COMMITTED` or incident | No automatic; diagnose |

**No-intent-no-rollback rule.** Rows C1, C4a, and C5a must report `rollback_attempted=false`. A caller that sees `rollback_attempted=true` is entitled to infer that a durable `ROLLBACK_INTENT` existed and the rename was attempted. The result must not lie about this distinction, because a later recovery decision depends on it.

**No automatic retry.** Compensation is never retried in-process. A second invocation must re-diagnose the state. `EXPLICIT_REAPPLY` is allowed only when the intent is durable and the observed state still matches the row's preconditions. A retry that changes the target/quarantine identities is a new incident, not a retry.

Forbidden compensation:

- Target path is occupied by an unknown object (identity != old, identity != temp). Never overwrite it to "restore".
- Quarantine identity/hash changed; the old content is not trustworthy.
- `nlink != 1` on quarantine; an alias exists.
- A `COMMITTED` record exists; the commit may have happened; recovery must diagnose, not roll back blindly.
- Binding fails; the path scene is not the session's scene.
- `ROLLBACK_INTENT` is not durable. The rename must not be attempted; the result is `ROLLBACK_NOT_AUTHORIZED`, not a failed rollback.

If compensation is forbidden, preserve both target and quarantine, append `RECOVERY_REQUIRED` with evidence, and return the same. A failed rollback is not a failed operation with no trace; it is an uncertain scene with a journal.


#### 7.3.2 Final rename succeeds but postcondition fails

This is `COMMIT_UNCERTAIN`:

- The target name may now refer to the temp object, or an attacker may have replaced it between rename and reopen.
- The old content is in quarantine (unless the journal says otherwise).
- Do not delete either. Do not report success. Do not "fix" by overwriting the target.
- Recovery classifies: target == temp -> committed; target == old -> not committed; target unknown -> incident; missing -> incident.
- If the target is ours but the journal lacks `COMMITTED`, recovery may record `RECOVERED_COMMITTED` without changing content; this is a metadata adoption, not a content adoption.

#### 7.3.3 Replace transition table

This table is the normative replace state machine. Each row is a boundary at which the process can stop. "Before" and "after" refer to the named side effect. The recovery column names the classification that Section 7.7 must reach from the observed state; it does not authorize an action by itself.

Ownership vocabulary used below:

- `old` = target identity/hash observed at `TARGET_OPENED`; `new` = temp identity/hash from `TEMP_READY`; `Q` = quarantine identity at `creation/quarantine/<batch_id>/<target-name>`; `?` = an object whose identity is neither `old` nor `new`.
- An object is **owned** only when its identity matches the identity recorded for that role in this operation's journal and its path is the expected role path. A path alone is never ownership.
- `target absent` means absence verified through the held target parent, not by `os.path.exists`.

| Step | Durable event | Precondition | Side effect | Postcondition | Failure class | Crash at this boundary -> recovery |
|---|---|---|---|---|---|---|
| R0 | `SESSION_OPEN` | root/lock/binding pass | none | all anchors bound | `REFUSED` | no operation; no action |
| R1 | `TARGET_OPENED` | target `old`; `nlink==1` | open target | identity/hash recorded | `REFUSED E_TARGET_CHANGED` | target `old`; no action |
| R2 | `QUARANTINE_DIR_READY` | quarantine dir absent | create `creation/quarantine/<batch_id>` | Q-dir identity recorded | `FAILED` | empty dir may remain; remove only if empty and identity matches |
| R3 | `TEMP_READY` | Q-dir owned; temp name absent | create/write/flush/verify temp | temp `new`, `nlink==1`, hash matches input | `FAILED E_TEMP_*` | orphan temp; delete only if identity is `new` |
| R4 | `QUARANTINE_INTENT` | target `old`, temp `new`, Q-dir owned | none | intent durable | `REFUSED` | target `old`, temp `new`; cleanup own temp |
| R5a | before target rename | target `old`; Q absent | none | no side effect | `REFUSED`/`FAILED` | same as R4 |
| R5b | target -> Q rename | target `old`; Q absent | rename via target handle, replace=false | target absent, Q `old` | `E_QUARANTINE_RENAME` | if target absent + Q `old`: `QUARANTINED`; if target `old` + Q absent: `FAILED`; anything else: incident |
| R6 | `QUARANTINED` | target absent, Q `old`, temp `new` | none | Q identity/hash, target absent | `RECOVERY_REQUIRED` if append fails | target absent + Q `old`: rollback candidate; Q `?`: incident |
| R7 | `COMMIT_INTENT` | target absent, Q `old`, temp `new` | none | intent durable | `REFUSED` | rollback candidate; no blind action |
| R8a | before commit rename | target absent, Q `old`, temp `new` | none | no side effect | `FAILED` | rollback candidate |
| R8b | temp -> target rename | target absent, Q `old`, temp `new` | rename via held parent, replace=false | target `new` | `E_COMMIT_CONFLICT` / `COMMIT_UNCERTAIN` | collision: if target absent, rollback candidate; if target `?`, incident; if target `new`, diagnose commit |
| R9 | `COMMITTED` | target `new`, Q `old` | none | target identity/hash/nlink, Q `old`, binding, parent flush | `COMMIT_UNCERTAIN` | target `new` + Q `old`: metadata adoption; target `?`: incident |
| R10 | terminal/close | target `new` | close handles | journal closed | `RECOVERY_REQUIRED` if close/flush fails | if `COMMITTED` durable, diagnose journal close; do not rewrite target |

Rules that the table makes explicit:

1. **Before R5b, a failure is a cleanup failure, not a content failure.** The target is still `old`; the temp is `new`; the operation may return `FAILED` after deleting only its own temp. It must not touch the target or quarantine.
2. **After R5b and before R8b, the only safe content action is a rollback candidate.** The target is absent by construction; a rollback is allowed only under all five conditions in Section 7.3.1 and only through the recovery transition table in Section 7.7.1. A direct in-operation retry of the target rename is not allowed after `QUARANTINED`; a new operation would collide with the missing target and confuse ownership.
3. **At R8b, a collision is not proof that the target is unknown.** Re-probe through the held parent. If the target is absent, the rename did not happen and a rollback candidate remains. If the target is `?`, preserve both objects and classify incident. If the target is `new`, the rename may have happened despite the error; classify as commit-uncertain and diagnose.
4. **Quarantine mismatch forbids rollback.** At R6, R8a, or R8b, if Q identity != `old`, Q hash != old hash, or Q `nlink != 1`, the old content is not trustworthy. Preserve Q and the target; return `RECOVERY_REQUIRED`/incident. Never rename Q over the target, and never delete Q to make the scene tidy.
5. **Final-rename failure is a state question, not an exception question.** The process must inventory target/Q/temp after any error from the rename; it must not assume the rename failed because the API returned an error, and must not assume it succeeded because the API returned success.
6. **Postcondition failure after a successful rename is `COMMIT_UNCERTAIN`.** The target may be `new`; Q is still `old`; no `COMMITTED` record exists. Recovery may adopt metadata only if the target is `new`; it may not roll back the target because that would delete the new content the operation intended to publish.
7. **Process termination at any row leaves only the objects in the postcondition column plus the previous row's state.** The journal may lag the filesystem by one event (for example, Q exists but `QUARANTINED` is missing). Recovery uses the observable identities, not the journal event alone.
8. **No row authorizes deleting an object whose identity is `?`.** A cleanup at R2/R3/R4 may delete only an empty owned Q-dir or an owned temp; a target or quarantine with `?` is evidence, not litter.

Idempotence and ownership rules:

| Repeated action | Already-done state | Required result |
|---|---|---|
| Delete own temp | temp absent | success/no-op; do not create a new temp |
| Delete own temp | temp `new`, `nlink==1`, no `COMMIT_INTENT` | delete; if `COMMIT_INTENT` exists, diagnose first |
| Delete own temp | temp `?` or `nlink>1` | incident; preserve |
| Rollback Q -> target | target `old`, Q absent | `ALREADY_ROLLED_BACK`; no second rename |
| Rollback Q -> target | target absent, Q `old`, conditions pass | one rename; verify; record `ROLLED_BACK` |
| Rollback Q -> target | target `new` | `COMMITTED`; do not roll back |
| Rollback Q -> target | target `?` | incident; preserve both |
| Metadata adoption | target `new`, Q `old`, `COMMITTED` already recorded | no-op |
| Metadata adoption | target `new`, Q `old`, no `COMMITTED` | append `RECOVERED_COMMITTED` only; no content change |

A second recovery run must reach the same classification from the new state and perform zero side effects if the first run completed. It must not use a fresh random name, a new batch ID, or a second target rename to "finish" the first operation.

### 7.4 Delete (design only; not admitted)

`delete` is not implemented in 2.4 (`writer.py:491-511` returns `E_NOT_IMPLEMENTED`). The production design keeps it out of the 2.5 admission scope. The safe shape, if approved later:

1. Open target; verify identity/hash/nlink; binding.
2. Create quarantine directory; journal `QUARANTINE_DIR_READY`.
3. `QUARANTINE_INTENT`; rename target -> quarantine, replace=false.
4. `QUARANTINED`; verify quarantine identity/hash/nlink; target absent.
5. Only after an explicit user confirmation **and** a separate `purge` request: `PURGE_INTENT`; delete the quarantine file through its handle; `PURGED`.
6. Crash before `PURGED` leaves the content in quarantine; recovery never auto-purges.

Delete must never call `DeleteFileW(target_path)` on the live target. Moving to quarantine is the backup. A "hard delete" that bypasses quarantine is not admitted.

### 7.5 Journal v2

#### 7.5.1 Canonical framing

There is one record-framing contract. The append path, reopen/verification path, tail repair, and recovery all call the same functions; no path may invent a different length or terminator rule.

| Constant | Value | Meaning |
|---|---|---|
| `MAX_RECORD_BYTES` | `65_536` | Maximum complete record line, **including** the trailing LF |
| `MAX_JSON_BYTES` | `65_535` | Maximum canonical JSON payload before LF |
| `MAX_JOURNAL_BYTES` | `8_388_608` | Maximum journal file size (8 MiB) |
| `MAX_RECORDS` | `100_000` | Maximum complete records |

A record is:

```
payload = canonical_json(record).encode("utf-8")   # strict, no surrogates
line    = payload + b"\n"
L       = len(line)
```

- `L <= 65_536` is accepted; `L == 65_536` is the exact maximum; `L == 65_537` is rejected with `E_JOURNAL_RECORD_TOO_LARGE`.
- The LF is part of the limit. A payload of 65,536 bytes plus LF is 65,537 bytes and is rejected.
- UTF-8 is strict. Unpaired surrogates are rejected before JSON encoding; no `ensure_ascii=True` escape may be used to smuggle them.
- Canonical JSON: `json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.
- No BOM. No CR. No blank lines. One object per line.
- The journal file must end with LF after every successful append. A file that does not end with LF is either a torn tail or an incomplete append; Section 7.5.4 decides which.

Append accepts a record only when all of these hold:

1. `L <= MAX_RECORD_BYTES`;
2. current complete record count `< MAX_RECORDS`;
3. `old_size + L <= MAX_JOURNAL_BYTES`;
4. `old_size == 0` or the last byte is `0x0A`;
5. the existing prefix validates under Section 7.5.4.

If the count or byte limit would be exceeded, the append fails with `E_JOURNAL_FULL` **before** writing any byte. If the last byte is not LF, the append fails with `E_JOURNAL_CORRUPT`; it does not append after a torn tail. The only path that may repair a tail is Section 7.5.4.

File name: `creation/operations/<batch_id>.journal.ndjson`. Open through the held `operations` anchor, never by path. Reject reparse, directory, and `nlink != 1`. Share `FILE_SHARE_READ`; desired `GENERIC_READ | GENERIC_WRITE | FILE_READ_ATTRIBUTES | SYNCHRONIZE`; no delete.

#### 7.5.2 Record schema

Required fields:

```json
{
  "schema_version": 2,
  "journal_id": "<batch_id>",
  "seq": 1,
  "operation": "create|replace|delete",
  "event": "SESSION_OPEN|...",
  "at": "2026-11-22T00:00:00Z",
  "root": {"volume_serial_number": 123, "file_id_hex": "..."},
  "target_relpath": "src/example.txt",
  "parent": {"volume_serial_number": 123, "file_id_hex": "..."},
  "target": null,
  "temp": null,
  "quarantine": null,
  "created_dirs": [],
  "chain_handoff": null,
  "prev_event_sha256": null,
  "payload": {}
}
```

Event-specific fields may be `null` but must be present. Never omit a field to mean "unknown"; use `null` plus a `payload.reason`. `chain_handoff` is `null` for an ordinary first record; after an archive it is an object in the new journal's first record (Section 7.5.6). The parser rejects unknown top-level fields only if `schema_version` is not understood; future minor additions require a new schema version.

`prev_event_sha256` is the SHA-256 of the exact UTF-8 bytes of the previous line **excluding** its trailing LF, or `null` for the first record. This binds the chain to the bytes on disk, not to a re-serialization. The parser recomputes each link.

#### 7.5.3 Append algorithm

```
old_size = GetFileSizeEx(handle)
if record_count >= MAX_RECORDS: raise E_JOURNAL_FULL
line = encode_record(record)                 # Section 7.5.1, includes LF
L = len(line)
if L > MAX_RECORD_BYTES: raise E_JOURNAL_RECORD_TOO_LARGE
if old_size + L > MAX_JOURNAL_BYTES: raise E_JOURNAL_FULL
if old_size > 0 and last_byte(handle) != 0x0A: raise E_JOURNAL_CORRUPT
write_all_at_eof(handle, line)
FlushFileBuffers(handle)
new_size = GetFileSizeEx(handle)
if new_size != old_size + L:
    try: SetEndOfFile(handle, old_size); FlushFileBuffers(handle)
    except: mark journal poisoned
    raise E_JOURNAL_APPEND
return new_size
```

Only after `append` returns may the caller perform the side effect it authorizes. `FlushFileBuffers` is the durability point. If it fails, no side effect. If the append wrote bytes but the size check fails, the truncation attempt is best-effort; if it also fails, the journal is poisoned and the operation cannot proceed.

The append must not re-serialize an existing line to compute `prev_event_sha256`; it hashes the exact bytes already on disk.

#### 7.5.4 Open, tail repair, and corruption

Let `data` be the file bytes read after the identity/reparse/nlink checks.

1. If `len(data) > MAX_JOURNAL_BYTES`, return `E_JOURNAL_TOO_LARGE`; do not truncate.
2. `last_lf = data.rfind(b"\n")`.
3. If `last_lf == -1` and `len(data) == 0`, the journal is empty: `E_JOURNAL_CORRUPT` (a valid operation journal has at least `SESSION_OPEN`).
4. If `last_lf == -1` and `len(data) > 0`, the entire file is a tail. It cannot be a valid operation journal; return `E_JOURNAL_CORRUPT` unless a separate recovery manifest proves a first record. There is no automatic repair of an empty-prefix journal.
5. `prefix = data[:last_lf + 1]`; `tail = data[last_lf + 1:]`.
6. Validate every complete line in `prefix` with `validate_record`: UTF-8, JSON object, `schema_version`, `journal_id`, contiguous `seq` from 1, `prev_event_sha256` chain, recognized `event`, consistent `operation`/root. Any failure in `prefix` is `E_JOURNAL_CORRUPT`; never truncate a complete-but-invalid prefix line.
7. If `tail` is empty, the journal is valid.
8. If `len(tail) > MAX_RECORD_BYTES`, return `E_JOURNAL_CORRUPT`; do not truncate an unbounded tail.
9. Classify the tail:
   - **Torn tail:** strict UTF-8 decode fails, or JSON parse fails. The append did not complete its record. Repair is allowed if `prefix` is non-empty and valid.
   - **Incomplete valid record:** UTF-8/JSON/schema/seq/hash all validate, but the LF is missing. The append did not return, so the record is not durable. Repair is allowed.
   - **Complete but invalid:** the bytes parse as JSON but fail schema/seq/hash/event/root validation. This is **not** a torn write. Return `E_JOURNAL_CORRUPT`; do not truncate.
10. Repair is a rewrite-and-replace, not an in-place truncation:

    a. Compute `truncate_to = last_lf + 1` and `truncated_bytes = len(data) - truncate_to`.
    b. Build `JOURNAL_TAIL_REPAIRED` with `old_journal_identity`, `old_size`, `truncate_to`, `truncated_bytes`, `tail_sha256`, `reason` (`torn_utf8`, `torn_json`, or `missing_lf_valid_record`), and `repair_method="atomic_replace"`.
    c. `new_data = prefix + encode_record(repair_record)`. If `new_data > MAX_JOURNAL_BYTES` or the repair line is over `MAX_RECORD_BYTES`, return `E_JOURNAL_FULL`; do not drop the tail without a record.
    d. Write `new_data` to a temp under `creation/operations/tmp`; flush, read back, verify size/hash, `nlink==1`.
    e. Recheck the journal identity and `old_size`; close the journal handle.
    f. Rename temp over the journal with replace=true (the journal is an internal object; this is the one permitted replace of an internal file). Reopen and verify identity, size, hash, and a fresh `scan_prefix`.
    g. If the rename fails, leave the old journal, delete only the owned temp, and return `E_JOURNAL_APPEND`/`RECOVERY_REQUIRED`.
    h. If the new file fails verification, preserve both objects if they still exist and return `RECOVERY_REQUIRED`.

11. Read-only diagnosis never repairs. It returns `E_JOURNAL_TAIL_UNREPAIRED` with `tail_offset`, `tail_sha256`, `reason`, and `repair_allowed=true/false`. Only an explicit apply under the lock may perform step 10.
12. If the journal is repaired, the operation result records `journal.tail_repaired=true`, the repair record's `seq`, and the old/new identities. The repair record is part of the new file, so there is no window in which the tail is gone without a record.

**Why rewrite instead of truncate?** An in-place truncation followed by an append has a crash window: the tail is gone, the repair record is not yet durable, and the next open sees a valid prefix with no evidence that a repair happened. The atomic replacement includes the repair record in the same namespace operation: a crash leaves either the old file (tail still present, repair can be retried) or the new file (repair record present). Neither state loses the audit trail.

A complete-but-invalid record is never repaired as a torn tail. A torn tail is only an unparseable or LF-incomplete final segment; a parseable record that fails validation is corruption or tampering.

#### 7.5.5 Journal events

| Event | When | Required evidence | Side effect after? |
|---|---|---|---|
| `SESSION_OPEN` | after lock and binding | root/operation/target/parent | no |
| `PARENT_DIRS_INTENT` | before creating missing parents | list, parent identity | yes (create dirs) |
| `PARENT_DIR_CREATED` | after each directory create | directory identity, parent identity, name | yes (next dir) |
| `PARENT_READY` | after parents | parent identity | no |
| `TARGET_ABSENT` | after probe | absent + parent | no |
| `TARGET_OPENED` | after target open/verify | before identity/hash/nlink | no |
| `CREATE_INTENT` | before temp | target parent, expected absent | yes (temp) |
| `QUARANTINE_DIR_READY` | after quarantine dir create | identity | no |
| `TEMP_READY` | after temp write/flush/verify | temp identity/hash/nlink | no |
| `QUARANTINE_INTENT` | before target rename | target/quarantine identities | yes (rename) |
| `QUARANTINED` | after target rename | quarantine identity, target absent | no |
| `COMMIT_INTENT` | before commit rename | temp identity, target name, quarantine | yes (rename) |
| `COMMITTED` | after commit + postcondition | target identity/hash/nlink | no |
| `ROLLBACK_INTENT` | before compensation rename | quarantine identity, target absent | yes (rename) |
| `ROLLED_BACK` | after compensation | target identity/hash == before | no |
| `RECOVERED_COMMITTED` | metadata adoption after a verified target, no content change | target identity/hash/nlink, `adopted_from` | no |
| `FAILED` | terminal failure | code, observed state | no |
| `RECOVERY_REQUIRED` | terminal uncertain | code, observed state | no |
| `JOURNAL_TAIL_REPAIRED` | inside the atomic repair file | old/new identity, sizes, tail hash, reason | no |
| `RECOVERY_INTENT` | before the one recovery side effect | classification, action, object identity, target absent | yes (one rename, delete, or rmdir) |
| `RECOVERY_DIAGNOSED` | recovery read-only | classification, evidence | no |
| `RECOVERY_APPLIED` | after explicit apply | before/after identity, action | depends |
| `PURGE_INTENT` / `PURGED` | future delete only | quarantine identity | yes |

`RECOVERY_INTENT.payload.action` is one of `ROLLBACK`, `DELETE_OWNED_TEMP`, `RESUME_CREATE`, `DISCARD_TEMP`, or `CLEAN_PARENT_DIRS`. An unknown action is `E_RECOVERY_REFUSED`.

A `FAILED` or `RECOVERY_REQUIRED` terminal event itself may fail to append. In that case the result is `RECOVERY_REQUIRED` with `journal_terminal=false`; do not report success.

#### 7.5.6 Journal archival and `E_JOURNAL_FULL`

**Current scope.** No archive implementation is admitted in 2.5R.1. `E_JOURNAL_FULL` is a fail-closed condition:

- The append is refused before any byte is written or any content side effect occurs.
- The journal is not truncated, rotated, renamed, or deleted automatically.
- No operation proceeds "best effort" without a journal.
- Diagnosis and recovery may still read the full journal; they do not append.
- The result includes `archive_required=true`; archival is an explicit future command, not an automatic action.

**Future archive contract.** The following is a requirement for a later phase, not a claim of current capability:

- **Archive path:** `creation/operations/archive/<journal_id>.<n>.ndjson`.
- **Metadata path:** `<archive>.meta.json`, written with the same safe-write boundary.
- **Metadata fields:** `archive_id`, `source_journal_id`, `source_identity`, `first_seq`, `last_seq`, `record_count`, `byte_count`, `first_prev_hash`, `last_record_hash`, `sha256`, `created_at`, `tool_version`, `reason`, and `chain_handoff`.
- **Archive operation:** write a complete copy to a temp, flush, verify length/hash/chain/sequence; rename to the archive path; write metadata temp, flush, verify, rename; append `JOURNAL_ARCHIVED` to the source; only then initialize a new journal. The source is not deleted until the archive and metadata verify.
- **Hash-chain handoff:** the new journal's first record has `chain_handoff = {archived_journal_id, archived_last_seq, archived_last_sha256}` and `prev_event_sha256=null`. Verification follows the handoff and records the chain boundary; it does not pretend the new file's first previous hash is part of the same byte chain.
- **Crash behavior:** a crash while copying leaves the source unchanged and a temp that may be deleted only if its identity is the archive temp. A crash after the archive rename but before metadata verification leaves the source in place; recovery either completes metadata or refuses. A crash after metadata but before the new journal header leaves the source readable and the archive verifiable; the next run may create the new header with the recorded handoff. No crash path deletes the source.
- **Verification failure:** if the archive hash/chain/sequence or metadata does not verify, keep the source, do not initialize a new journal, and return `E_JOURNAL_ARCHIVE_VERIFY` (`RECOVERY_REQUIRED`). Do not fall back to deleting or truncating the source.
- **Recovery from archive-pending:** the state is recorded in metadata and a dedicated `JOURNAL_ARCHIVED`/handoff record; a second run is idempotent and never creates a second archive for the same sequence range.

Until this contract is implemented and tested, `E_JOURNAL_FULL` remains a safe, non-destructive refusal. A design that says "archive automatically" without the above is not admissible.


### 7.6 Crash matrix

The recovery classifier must handle at least these observable states. `old` is the target identity/hash at `TARGET_OPENED`; `new` is the temp identity/hash at `TEMP_READY`; `Q` is the quarantine identity/hash; `?` is an object whose identity is neither `old` nor `new`. Each row has exactly **one** classification. The classification is an input to Section 7.7; it does not authorize an action by itself.

| ID | Operation | Last durable event | Target | Quarantine | Temp | Exactly one classification |
|---|---|---|---|---|---|---|
| C-01 | create | before `PARENT_DIRS_INTENT` | absent | n/a | absent | `CREATE_CLEAN` |
| C-02 | create | `PARENT_DIRS_INTENT`/`PARENT_DIR_CREATED`, no `PARENT_READY` | absent | n/a | absent | `CREATE_PARENT_PARTIAL` |
| C-03 | create | `PARENT_READY` | absent | n/a | absent | `CREATE_NOT_STARTED` |
| C-04 | create | `TARGET_ABSENT` | absent | n/a | absent | `CREATE_NOT_STARTED` |
| C-05 | create | `CREATE_INTENT` | absent | n/a | absent | `CREATE_INTENT_NO_TEMP` |
| C-06 | create | `CREATE_INTENT` | absent | n/a | present, identity not recorded | `CREATE_TEMP_UNRECORDED` |
| C-07 | create | `TEMP_READY` | absent | n/a | `new`, nlink==1 | `ORPHAN_TEMP` |
| C-08 | create | `TEMP_READY` | absent | n/a | `?` or nlink>1 | `CREATE_TEMP_UNKNOWN` |
| C-09 | create | `COMMIT_INTENT` | absent | n/a | `new`, nlink==1 | `CREATE_COMMIT_NOT_OBSERVED` |
| C-10 | create | `COMMIT_INTENT` | `new`, nlink==1 | n/a | absent | `COMMITTED_WITHOUT_RECORD` |
| C-11 | create | `COMMITTED` | `new`, nlink==1 | n/a | absent | `COMMITTED` |
| C-12 | create | any | `?` | n/a | any | `CREATE_TARGET_UNKNOWN` |
| C-13 | create | any | `new` or `?` | n/a | `new` | `CREATE_DUPLICATE_OBJECTS`; `HARDLINK` if target/temp share one identity and nlink>1 |
| C-14 | create | `COMMIT_INTENT` | absent | n/a | absent | `CREATE_TEMP_LOST` |
| C-15 | create | any | `old` (a create target that already existed) | n/a | any | `CREATE_TARGET_UNKNOWN` |
| R-01 | replace | before `TARGET_OPENED` | `old` | absent | absent | `TARGET_UNCHANGED` |
| R-02 | replace | before `TARGET_OPENED` | absent or `?` | absent | absent | `TARGET_MISSING` or `TARGET_UNKNOWN`; the observed absence/identity decides, not the path |
| R-03 | replace | `TARGET_OPENED` | `old` | absent | absent | `TARGET_UNCHANGED` |
| R-04 | replace | `QUARANTINE_DIR_READY` | `old` | absent or empty owned Q-dir | absent | `QUARANTINE_DIR_ONLY` |
| R-05 | replace | `TEMP_READY` | `old` | empty owned Q-dir | `new` | `TEMP_ONLY` |
| R-06 | replace | `QUARANTINE_INTENT` | `old` | absent | `new` | `QUARANTINE_INTENT_NOT_EXECUTED` |
| R-07 | replace | `QUARANTINE_INTENT` | `old` | `old` | `new` | `QUARANTINE_APPEARED` |
| R-08 | replace | `QUARANTINED` | absent | `old` | `new` | `SAFE_ROLLBACK` |
| R-09 | replace | `COMMIT_INTENT` | absent | `old` | `new` | `SAFE_ROLLBACK` |
| R-10 | replace | `COMMIT_INTENT` | `old` | `old` | `new` | `TARGET_RESTORED_BY_OTHER` |
| R-11 | replace | `COMMIT_INTENT` | `new` | `old` | absent | `COMMITTED_WITHOUT_RECORD` |
| R-12 | replace | `COMMITTED` | `new` | `old` | absent | `COMMITTED` |
| R-13 | replace | `COMMITTED` | `?` | `old` | absent | `POSTCONDITION_INCIDENT` |
| R-14 | replace | `ROLLBACK_INTENT` | absent | `old` | `new` | `ROLLBACK_IN_PROGRESS` |
| R-15 | replace | `ROLLED_BACK` | `old` | absent | `new` | `ALREADY_ROLLED_BACK` |
| R-16 | replace | any | `?` | any | any | `UNKNOWN_TARGET` |
| R-17 | replace | any | any | `?` | any | `QUARANTINE_MISMATCH` |
| J-01 | any | journal prefix invalid | any | any | any | `CORRUPT_JOURNAL` |
| J-02 | any | journal tail unrepairable in read-only mode | any | any | any | `JOURNAL_TAIL_UNREPAIRED` |
| J-03 | any | `nlink > 1` on target/Q/temp | any | any | any | `HARDLINK` |
| J-04 | any | binding mismatch | any | any | any | `RECOVERY_REFUSED` |

A row marked `SAFE_ROLLBACK` is still not permission: Section 7.3.1's five conditions and Section 7.7.2's preconditions must both hold at apply time. If they do not hold, the state is re-classified as `UNKNOWN_TARGET`, `QUARANTINE_MISMATCH`, `HARDLINK`, or `RECOVERY_REFUSED`; there is no best-effort rollback.

### 7.7 Recovery

Recovery has two modes:

1. **Diagnose (default):** read-only. If the lock is available, take it; otherwise open root/creation/operations read-only and read the journal. Inventory target, quarantine, temp, and identities. Return a classification and evidence. Do not append unless the journal is writable and the caller explicitly asked to record the diagnosis; read-only diagnosis must work even if the journal is read-only.
2. **Apply (explicit):** acquire the lock; verify binding; re-read the journal; re-inventory; require the classification to be one of the allowed cases; then follow Section 7.7.2. Apply performs at most one content side effect per invocation. It never batches multiple operations.

#### 7.7.1 Recovery protocol and ownership

Recovery has its own durable intent. `RECOVERY_INTENT` is appended before the one content side effect; `RECOVERY_APPLIED` (or the operation-specific terminal event) is appended only after the postcondition is verified. If the process stops after the intent but before the side effect, the next diagnosis sees the same state and may retry the same transition. If it stops after the side effect but before the terminal record, the next diagnosis classifies the resulting state (`ALREADY_ROLLED_BACK`, `COMMITTED`, `ORPHAN_TEMP_CLEANED`, `ALREADY_CLEANED`, or `RECOVERED_COMMITTED`) and records metadata without repeating the side effect.

Ownership rules for every recovery transition:

1. **Identity plus role path.** A target is owned only if its identity is `new` (commit) or `old` (rollback) and it is the exact relative target path. A quarantine is owned only if its identity is `old` and it is under `creation/quarantine/<batch_id>/` for this operation. A temp is owned only if its identity is `new` and it is under `creation/operations/tmp/` with the operation's batch prefix. A created parent directory is owned only if it has a `PARENT_DIR_CREATED` identity and is in the target parent chain.
2. **No ownership by name.** A file with the expected name but an unexpected identity is `?`. It is never overwritten, renamed over, or deleted. A file with the expected identity but an unexpected path is also not silently adopted; report an incident.
3. **No delete of unidentifiable objects.** Recovery may delete only an owned temp and, under Section 7.2.5, an owned empty parent directory. It never deletes a target, a quarantine, or an unrecorded directory. `ORPHAN_QUARANTINE` requires explicit purge outside this table.
4. **No overwrite on rollback or resume.** The target must be absent at the content action. A target that appears between the intent and the action is re-checked through the held parent; if it is `old`, recovery reports `ALREADY_ROLLED_BACK`; if it is `new`, recovery reports `COMMITTED`; if it is `?`, incident. It never uses replace=true.
5. **Idempotence is state-based, not record-based.** Re-running a rollback after completion sees target `old` + Q absent and performs no rename. Re-running metadata adoption sees the record and performs no append. Re-running temp cleanup sees temp absent and performs no delete. A second run must not generate a new batch ID or a new object name.
6. **One side effect per invocation.** If a later step is required (for example rollback then temp cleanup), the operator invokes apply again after diagnosis; recovery does not chain renames in one call.
7. **Journal append failure never blocks diagnosis.** If `RECOVERY_INTENT` or the terminal record cannot be appended, the result is `RECOVERY_REQUIRED` with `journal_terminal=false`; no content action is taken on that invocation.
8. **No automatic apply.** A process-crash recovery does not run automatically on startup. The CLI requires an explicit `--apply` and a confirmation; the library defaults to `diagnose`.

#### 7.7.2 Recovery apply actions

This is the only table that authorizes a recovery side effect. "One" means one content mutation per invocation. Metadata appends are not content mutations and may accompany the action.

| Action | Allowed classifications | Preconditions | Required durable intent | One side effect | Postcondition | Terminal evidence | Result |
|---|---|---|---|---|---|---|---|
| `APPEND_METADATA` | `COMMITTED_WITHOUT_RECORD`, `ALREADY_ROLLED_BACK` | target identity/hash/nlink matches the expected role; no content change needed; binding | none | none (metadata append only) | terminal record durable | `RECOVERED_COMMITTED` or `ROLLED_BACK` | `SUCCEEDED` if terminal durable; else `RECOVERY_REQUIRED` |
| `ROLLBACK` | `SAFE_ROLLBACK` | target absent; Q `old`, hash match, nlink==1; no `COMMITTED`; binding; lock | `RECOVERY_INTENT` action `ROLLBACK` | rename Q -> target, replace=false | target `old`; Q absent; identity/hash/nlink verified | `ROLLED_BACK` | `FAILED` with `restored=true` |
| `DELETE_OWNED_TEMP` | `ORPHAN_TEMP` | temp `new`, nlink==1; target absent; no `COMMIT_INTENT`; binding | `RECOVERY_INTENT` action `DELETE_OWNED_TEMP` | delete temp through its handle | temp absent; target still absent | `RECOVERY_APPLIED` | `FAILED` after cleanup; `RECOVERY_REQUIRED` if delete fails |
| `RESUME_CREATE` | `CREATE_COMMIT_NOT_OBSERVED` | target absent; temp `new`, hash/nlink match `TEMP_READY`; no `COMMITTED`; binding; lock | `RECOVERY_INTENT` action `RESUME_CREATE` | rename temp -> target, replace=false | target `new`; temp absent; identity/hash/nlink verified | `RECOVERED_COMMITTED` + `RECOVERY_APPLIED` | `SUCCEEDED` with `recovery.applied=true` |
| `DISCARD_TEMP` | `CREATE_COMMIT_NOT_OBSERVED` | temp `new`, hash/nlink match `TEMP_READY`; target absent; no `COMMITTED`; binding; lock | `RECOVERY_INTENT` action `DISCARD_TEMP` | delete temp through its handle | temp absent; target absent | `RECOVERY_APPLIED` | `FAILED` after cleanup |
| `CLEAN_PARENT_DIRS` | `CREATE_PARENT_PARTIAL` | recorded `PARENT_DIR_CREATED`; identity matches; empty; target/temp absent; binding; lock | `RECOVERY_INTENT` action `CLEAN_PARENT_DIRS` | rmdir one owned empty directory | directory absent; parent bound | `RECOVERY_APPLIED` | `FAILED` after cleanup; `created_dirs_retained` if refused |
| `NONE` | `COMMITTED`, `TARGET_UNCHANGED`, `CREATE_NOT_STARTED`, `CREATE_CLEAN`, `CREATE_INTENT_NO_TEMP`, `CREATE_TEMP_LOST`, `UNKNOWN_TARGET`, `TARGET_MISSING`, `TARGET_RESTORED_BY_OTHER`, `QUARANTINE_APPEARED`, `QUARANTINE_INTENT_NOT_EXECUTED`, `QUARANTINE_MISMATCH`, `POSTCONDITION_INCIDENT`, `HARDLINK`, `CORRUPT_JOURNAL`, `JOURNAL_TAIL_UNREPAIRED`, `RECOVERY_REFUSED` | classification is read-only | none | none | report only | `RECOVERY_DIAGNOSED` (optional) | classification result; no mutation |

`CREATE_TEMP_UNRECORDED`, `CREATE_TEMP_UNKNOWN`, `ORPHAN_QUARANTINE`, and `CREATE_DUPLICATE_OBJECTS` are `NONE` with an incident report: there is no safe automatic action because ownership is not proven.

The old "allowed automatic content changes" wording is replaced by this table. There is **no automatic content change**. `APPEND_METADATA` is the only no-content adoption, and it still requires an explicit apply and a durable terminal record.

Recovery must never:

- delete a user file that is not an identified temp or an owned empty parent directory of this operation;
- overwrite or rename over an unknown target;
- recursively delete a directory;
- operate outside the root;
- ignore `nlink`;
- trust a path without identity;
- "repair" by truncating a complete journal record;
- roll back after a `COMMITTED` record or after the target has been observed as `new`;
- resume a create without a durable `RECOVERY_INTENT` and all six preconditions.

If apply fails, return `RECOVERY_REQUIRED` with the new state and leave the journal append-only. If the journal cannot be appended, the content operation may still be diagnosable; report `journal_terminal=false`. Do not retry automatically.


### 7.8 Failure mapping

A code alone does not determine the public status. The mapping depends on **where** the failure occurred relative to the last durable intent and the content side effect. The same code may be `REFUSED` before any content change and `RECOVERY_REQUIRED` after one. This table is normative; no caller may hard-code `code -> SUCCEEDED`.

#### 7.8.1 Status selection

1. If a precondition fails before any content side effect, the result is `REFUSED` unless the operation had already created a temp or directory that must be cleaned; then it is `FAILED` only if cleanup completed and the target was untouched, otherwise `RECOVERY_REQUIRED`.
2. If the required intent event is not durable, no content side effect is permitted. The result is `REFUSED` with `rollback_attempted=false`, even if the caller asked for compensation.
3. If a content side effect occurred but its postcondition is verified and only a non-content metadata/terminal event failed, the result is `RECOVERY_REQUIRED` with `journal_terminal=false`; it is never `SUCCEEDED`.
4. If the target's final state is unknown, the result is `COMMIT_UNCERTAIN` or `RECOVERY_REQUIRED`; never `SUCCEEDED`.
5. A `SUCCEEDED` result requires all of: target identity/hash/nlink verified, content flush verified, journal terminal durable, and no unresolved classification.

#### 7.8.2 Code mapping

| Code | Before content side effect | After content side effect | Journal terminal | Required behavior |
|---|---|---|---|---|
| `E_INVALID_PATH` | `REFUSED` | n/a | none | no handle opened |
| `E_UNSUPPORTED_VOLUME` | `REFUSED` | n/a | none | volume facts before open |
| `E_REPARSE_REFUSED` | `REFUSED` | `RECOVERY_REQUIRED` if a side effect already occurred | none/uncertain | close all; never follow |
| `E_ROOT_UNTRUSTED` | `REFUSED` | n/a | none | no implicit enrollment |
| `E_ROOT_CHANGED` / `E_ROOT_MISSING` | `REFUSED` | `RECOVERY_REQUIRED` | none/uncertain | close chain; do not update trust |
| `E_TRUST_RECORD_*` | `REFUSED` | `RECOVERY_REQUIRED` | none/uncertain | fail closed; no auto-repair |
| `E_BINDING_CHANGED` | `REFUSED` | `RECOVERY_REQUIRED` | none/uncertain | observe, do not write; diagnose |
| `E_LOCK_BUSY` | `REFUSED` | n/a | none | no waiting by default |
| `E_LOCK_CHANGED` | `RECOVERY_REQUIRED` | `RECOVERY_REQUIRED` | uncertain | incident; do not proceed |
| `E_LOCK_METADATA` | `REFUSED` | n/a | none | release lock; no operation |
| `E_TARGET_EXISTS` | `REFUSED` | `RECOVERY_REQUIRED` if the target appeared during commit | none | no overwrite; temp cleanup only |
| `E_TARGET_MISSING` | `REFUSED` | `COMMIT_UNCERTAIN` / `RECOVERY_REQUIRED` | none/uncertain | replace/delete only; diagnose |
| `E_TARGET_CHANGED` | `REFUSED` | `RECOVERY_REQUIRED` or `POSTCONDITION_INCIDENT` | none/uncertain | identity/hash/nlink mismatch; preserve |
| `E_HARDLINK_REFUSED` | `REFUSED` | `RECOVERY_REQUIRED` | none/uncertain | before: refuse; after: preserve |
| `E_JOURNAL_RECORD_TOO_LARGE` | `REFUSED` | `RECOVERY_REQUIRED` | none/uncertain | pre-append; never truncate to fit |
| `E_JOURNAL_FULL` | `REFUSED` | `RECOVERY_REQUIRED` | none/uncertain | pre-append; no auto archive; `archive_required=true` |
| `E_JOURNAL_APPEND` | `REFUSED` if the intent itself failed; `RECOVERY_REQUIRED` if a prior side effect exists | `RECOVERY_REQUIRED` with `restored`/`journal_terminal` flags | none/uncertain | see Section 7.8.3; never claim rollback was attempted without `ROLLBACK_INTENT` |
| `E_JOURNAL_CORRUPT` | `REFUSED` | `RECOVERY_REQUIRED` | none/uncertain | no automatic repair of complete bad records |
| `E_JOURNAL_TAIL_UNREPAIRED` | `RECOVERY_REQUIRED` | `RECOVERY_REQUIRED` | none | read-only diagnosis; explicit apply may repair |
| `E_TEMP_CREATE` | `FAILED`/`REFUSED` | n/a | `FAILED` if durable | temp only; target unchanged; cleanup |
| `E_TEMP_WRITE` / `E_TEMP_VERIFY` | `FAILED` | n/a | `FAILED` if durable | temp only; target unchanged; cleanup |
| `E_COMMIT_CONFLICT` | `REFUSED` if observed before rename; `COMMITTED` if target is verified `new`; `RECOVERY_REQUIRED` if `?` | `RECOVERY_REQUIRED` | none/uncertain | no overwrite; diagnose |
| `E_COMMIT_UNCERTAIN` | n/a | `COMMIT_UNCERTAIN` | uncertain | preserve; no automatic retry |
| `E_ROLLBACK_FAILED` | n/a | `RECOVERY_REQUIRED` | uncertain | preserve; diagnose |
| `E_FLUSH_CONTENT` | `FAILED` | `RECOVERY_REQUIRED` | `FAILED`/uncertain | content postcondition not verified; no success |
| `E_FLUSH_DIRECTORY` | n/a | `SUCCEEDED` with `durability_claim=DIRECTORY_FLUSHED` if target verified; else `RECOVERY_REQUIRED` | terminal durable | directory-entry durability unverified |
| `E_FLUSH_JOURNAL_TERMINAL` | n/a | `RECOVERY_REQUIRED` / `COMMIT_UNCERTAIN` | not durable | content may be correct; do not report success |
| `E_JOURNAL_VERIFY` | `REFUSED` | `RECOVERY_REQUIRED` | uncertain | reopen/verify failure; preserve |
| `E_IO` | `FAILED`/`REFUSED` | `RECOVERY_REQUIRED` | depends | map after observation; never blind success |
| `E_LIMIT` | `REFUSED` | `RECOVERY_REQUIRED` if after side effect | none/uncertain | path/journal/resource limit |
| `E_RECOVERY_REFUSED` | `RECOVERY_REQUIRED` | `RECOVERY_REQUIRED` | none | classification not allowed; no mutation |
| `E_RECOVERY_NOT_ALLOWED` | `RECOVERY_REQUIRED` | `RECOVERY_REQUIRED` | none | apply requested without authorization/precondition |
| `E_ORPHAN_TEMP_DELETE_FAILED` | n/a | `RECOVERY_REQUIRED` | uncertain | temp preserved; report identity |
| `E_ARCHIVE_*` (future) | `REFUSED` | `RECOVERY_REQUIRED` | none/uncertain | not implemented in 2.5R.1 |

`E_DURABILITY_UNCERTAIN` is a legacy umbrella and must not be emitted by new code; it is replaced by `E_FLUSH_CONTENT`, `E_FLUSH_DIRECTORY`, `E_FLUSH_JOURNAL_TERMINAL`, and `E_JOURNAL_VERIFY`. If an older caller sees it, treat it as `RECOVERY_REQUIRED` and map to the specific condition from the result fields.

#### 7.8.3 Journal append during compensation

This table resolves the R2 contradiction. `rollback_attempted` is true **only if** `ROLLBACK_INTENT` was durably appended.

| Case | Durable `ROLLBACK_INTENT`? | Content side effect allowed? | Result | Recovery classification | Automatic retry |
|---|---|---|---|---|---|
| C1 `ROLLBACK_INTENT` append fails | no | **no** | `RECOVERY_REQUIRED` (or `FAILED` if no side effect yet) | `SAFE_ROLLBACK` remains, but no compensation was attempted; `rollback_attempted=false` | forbidden |
| C2 appended, rename fails | yes | yes, attempted | `RECOVERY_REQUIRED` | target absent + Q `old` + temp `new` => `ROLLBACK_IN_PROGRESS` | forbidden |
| C3 rename succeeds, `ROLLED_BACK` append fails | yes | yes, completed | `RECOVERY_REQUIRED`; `restored=true`; `journal_terminal=false` | `ALREADY_ROLLED_BACK` | forbidden |
| C4 journal corrupt/full during compensation | depends | only if the specific intent is already durable | `RECOVERY_REQUIRED`; no further append | `CORRUPT_JOURNAL` / `JOURNAL_TAIL_UNREPAIRED` | forbidden |
| C5 binding changes before/during compensation | depends | **no new side effect** | `RECOVERY_REQUIRED` | `RECOVERY_REFUSED` | forbidden |
| C6 `COMMITTED` already exists | n/a | **no rollback** | `SUCCEEDED` if target verified; else `RECOVERY_REQUIRED` | `COMMITTED` / `POSTCONDITION_INCIDENT` | forbidden |

A failed `ROLLBACK_INTENT` append is not an attempted rollback. The result must not say "rollback attempted" unless C2/C3/C4 with a durable intent. If the journal is poisoned before the intent, the only safe report is `RECOVERY_REQUIRED` with `rollback_attempted=false` and `journal_terminal=false`.

#### 7.8.4 Retry rules

- `SUCCEEDED`: no retry.
- `REFUSED` with no side effect: retry is allowed only after the caller fixes the precondition (new operation, new batch).
- `FAILED` after temp cleanup: a new operation may retry; it must not reuse the batch ID.
- `COMMIT_UNCERTAIN`: no automatic retry and no automatic rollback; diagnose first.
- `RECOVERY_REQUIRED`: no automatic retry; explicit `recover --apply` only.
- `PASS WITH ACCEPTED RESIDUAL`: not a write status; it is a gate status and never appears in `OperationResult`.


## 8. Prototype remediation requirements

The 2.4 prototype is not the production implementation. This section turns the independent audit into concrete requirements. Statuses reflect **Phase 2.4R remediation**, independently rechecked against the current tree; a requirement marked **P0** still blocks production admission; **P1** blocks a reliability claim; **P2** is correctness/documentation debt that must still be closed before the design is considered fully implemented. "CLOSED" means the specific defect was fixed and regression-tested; it does not mean the production invariant is admitted.

### 8.1 P0 remediation

#### R-01 - Root-chain leak on expected-identity mismatch (`P0`)

2.4 audit: `anchors.py:130-156` opened the chain inside a `try`, but the expected-identity comparison was **outside** that `try`; on mismatch the whole chain leaked (50 failures -> +400 handles, 8 per failure).

2.4R status: **CLOSED.** The comparison is now inside the protected `try` (`anchors.py:165-166`), and failure closes the chain in reverse order (`:167-170`). Independent probe: 25 mismatches -> **0** handle delta. `tests/test_creation_safewriter.py:134` asserts the handle count returns to baseline.

Required at admission:

- Keep the regression test; it must remain able to fail if the comparison moves outside the cleanup region.
- Add a mismatch at an **intermediate** component (not only the final root) and assert no leak.
- `RootAnchor.open` must return a chain only if every component, the final identity, the volume serial, and the reparse checks pass.

Why: a refused open that leaks handles is a denial-of-service vector and can mask the identity failure.

#### R-02 - RootAnchor.open_path` leak on a missing/intermediate child (`P0`)

2.4 audit: `anchors.py:225-229` looped `open_child` without `try/finally`; a later failure leaked earlier ancestors (50 calls -> +50 handles).

2.4R status: **CLOSED.** `open_path` records opened anchors and closes them deepest-first (`anchors.py:229-244`); `tests/test_creation_safewriter.py:147` covers a missing intermediate component; independent probe: 0 handle delta over 25 iterations.

Required at admission:

- Extend the test matrix to a non-directory component, a reparse component, and an identity mismatch at each position.
- On success, ownership transfers to the returned `DirectoryAnchor` chain exactly once.

#### R-03 - _open_session` leak when `root.verify()` fails (`P0`)

2.4 audit: `writer.py:538-570` called `RootAnchor.open` and `root.verify()` before the inner cleanup `try`; a verify failure leaked the root chain.

2.4R status: **CLOSED.** `root.verify()` now runs inside the `_open_session` cleanup region (`writer.py:538-572`); failure closes journal/lock/operations/creation/root. The fault-injection regression is in the focused suite.

Required at admission:

- Keep one outer `try/finally` from the first handle; every partially constructed object is closed exactly once.
- Add a test that makes `verify()` raise after a successful open and asserts zero handle delta and no directory/journal side effect.

#### R-04 - _ensure_create_parent` leak on journal append failure (`P0`)

2.4 audit: `writer.py:591-615` opened the parent chain, closed intermediates, then appended `PARENT_READY`. If the append failed, `parent` had not been returned to the caller, so the caller's `finally` could not close it.

2.4R status: **CLOSED.** `_ensure_create_parent` closes the parent chain when the append fails (`writer.py:608-624`); the caller receives either a fully owned chain or none.

Required at admission:

- Keep the PARENT_READY fault-injection test and assert no handle leak and no target write.
- Do not move ownership transfer before the journal append.

#### R-05 - Temp creation can orphan and is not nlink-checked (`P0`)

2.4 audit: `writer.py:697-745` created the temp in the target's parent (`<target>.veyra-<random>.tmp`). On `OSError` it only closed the handle, so the caller could not clean up; a real second-write failure left a 0-byte `.veyra-*.tmp` in the project.

2.4R status: **PARTIAL.** `_cleanup_temp` now marks delete-on-close and reports whether the mark succeeded (`writer.py:753-769`); on a failed mark the structured error names the retained temp. `tests/test_creation_safewriter.py:254` now injects the failure only at the temp payload write, proves the temp existed, and asserts no `.tmp` remains after cleanup. **Still open:** the temp is still created in the business parent, there is no `TEMP_READY` nlink check, and there is no nlink recheck before commit.

Required:

- Create the temp in `creation/operations/tmp/<batch_id>.<random>.tmp`, not in the business directory.
- The temp factory has a single owner: on any failure it deletes the temp it created (delete-on-close or anchored delete), or returns `RECOVERY_REQUIRED` with the path/identity if deletion fails.
- Journal `TEMP_READY` only after write + flush + read-back hash/size + `nlink == 1`.
- Recheck `nlink == 1` immediately before `COMMIT_INTENT`, after the rename, and on the final target handle.
- If a temp with `nlink > 1` is observed, refuse; do not delete it silently (an alias may exist). Report `E_HARDLINK_REFUSED`/`RECOVERY_REQUIRED` with identity.
- Add tests for: temp create failure, first write failure, second write failure, flush failure, read-back mismatch, delete-on-close failure, nlink>1 at each checkpoint, and orphan temp recovery.

#### R-06 - Binding revalidation is incomplete and untested (`P0`)

Current: `writer.py:626-656` reopens only the root and the business parent. It does not revalidate `creation`, `operations`, the journal identity, the lock identity, or the quarantine anchor. A 2.5 mutation run that replaces `_verify_binding` with a no-op still passes all 26 focused tests (`Ran 26 tests ... OK`).

Required:

- Implement the session-level binding in Section 4.7 for all anchors.
- Make `verify_binding` return a structured `BindingResult` with every component's expected/actual identity; a mismatch is `E_BINDING_CHANGED` and includes the component name.
- Add tests that swap each component in turn: root, ancestor, creation, operations, journal, lock, quarantine, target parent, target; each must fail with the right component and no side effect.
- Add a mutation test: disable the check and assert the suite **fails**. The current suite must not be considered valid until this mutation is caught.
- Add a test that swaps a component **after** the last pre-commit check but before rename, using a fault hook; the post-rename binding check must catch it or the operation must be `COMMIT_UNCERTAIN`. This is the check-then-use window; the design does not claim it is impossible, only that it is detected and never silently reported as success.

#### R-07 - Directory share modes allow root/parent rename (`P0`)

Current (2.4R): `anchors.py:139`, `:305`, `:316`, `:326` still use `FILE_SHARE_ALL`; the expected `READ | WRITE` (no delete) mode is not implemented.

Required:

- All directory anchors, including `open_volume_root`, use `FILE_SHARE_READ | FILE_SHARE_WRITE` (no delete).
- Keep `FILE_OPEN_REPARSE_POINT` and `FILE_DIRECTORY_FILE`.
- Add tests:
  - rename root while only the root chain is open -> sharing violation;
  - rename an ancestor while the chain is open -> sharing violation;
  - rename a business parent while its anchor is open -> sharing violation;
  - rename a child file/dir while the parent is no-delete -> must still succeed (the commit path must not be blocked);
  - close the session -> rename succeeds.
- Document the observed 2.4 attribution correction: the old rename failure came from a live descendant file handle, not the directory handle. A test that only opens the root and expects a rename failure would fail with the corrected share mode; it must be written against the intended invariant, not the old accident.

#### R-08 - Replace has no compensation (`P0`)

Current: `writer.py:388-489` returns `RECOVERY_REQUIRED` if the final rename fails after quarantine; the old target is left in quarantine and the target path is absent. The 2.4 report explicitly lists this as a known limitation.

Required:

- Implement Section 7.3.1: verify the five compensation conditions; append `ROLLBACK_INTENT`; rename quarantine -> target with replace=false; verify identity/hash/nlink; append `ROLLED_BACK`; return `FAILED` with `restored=true`.
- If any condition is false, do not compensate; return `RECOVERY_REQUIRED` with evidence.
- Add tests: commit failure before target creation (safe rollback), commit failure with target occupied (no overwrite), quarantine identity changed (no rollback), quarantine nlink>1 (no rollback), rollback rename failure (uncertain), rollback success followed by `ROLLED_BACK` append failure (uncertain), and a `COMMITTED` record present (no blind rollback).
- The rollback itself must be journaled and must not overwrite an unknown target. This is a content change, not a metadata cleanup; it needs the same identity discipline as the forward path.

#### R-09 - Lock release reopens by path and can be raced (`P0`)

Current: `lock.py` `LockHandle.release()` reopens `executor.lock` with share `READ|WRITE`, verifies identity, unlocks, closes. The re-open is by path under `creation`; if the lock file was replaced between acquire and release (which should be blocked by the lock file's share mode, but the code must not rely on an unverified assumption), release can unlock the wrong file or fail.

Required:

- Hold the lock file handle for the whole session. `release()` unlocks through the held handle and closes it; it never reopens by path.
- If a reopen is ever needed for diagnosis, it is read-only and identity-checked; it never authorizes an unlock.
- Add a test that attempts to delete/rename the lock file while held (expect 32) and a test that closes/reopens a new `SafeWriter` after a simulated crash (stale file, no lock).
- `LockManager.acquire` must close the lock handle on every failure path, including owner-metadata write/flush failure.

#### R-10 - Journal tail policy contradicts its docstring (`P0`)

Current: `journal.py` says a missing trailing newline is corruption in the docstring, but the code truncates the incomplete tail and appends `JOURNAL_TAIL_REPAIRED`. There is no explicit distinction between a torn append and a complete-but-invalid record.

Required:

- Implement journal v2 (Section 7.5): length/record limits, append offset, size verification, rollback, tail repair only for an incomplete non-JSON tail, corruption for a complete-but-invalid record, `JOURNAL_TAIL_REPAIRED` record, hash chain over raw line bytes.
- Test: crash after partial write without LF; crash after partial write with LF but incomplete JSON; complete line with bad hash; complete line with bad seq; complete line with unknown event; oversized record; journal full; flush failure; truncate failure; middle corruption; duplicate seq; wrong batch ID.
- Test that a repaired tail is recorded and visible, not silent.
- Test that the journal file cannot be renamed/deleted while held (share + no-delete parent).

#### R-11 - Journal and lock are not part of the binding (`P0`)

Current: `_verify_binding` does not query the held journal or lock identity. A second process could create a new `creation/` and lock while the first process reports success against the old handles.

Required:

- Add both to `verify_binding` (Section 4.7); compare held-handle identity and path-reopened identity.
- Add a test that replaces `creation` after the first operation and before a second operation; the second must fail `E_BINDING_CHANGED` and must not write into the new `creation`.
- Add a test that replaces the journal file after `TEMP_READY`; append must fail, not continue into a new file.
- Add a test that replaces the lock file after acquire (should be blocked by share; if forced by a test hook, `E_LOCK_CHANGED`).

#### R-12 - Root trust is optional and first-use is implicit (`P0`)

Current: `RootAnchor.open` accepts `expected_root_identity=None` and then records the opened identity. `SafeWriter` can be constructed without a trust record.

Required:

- Strict mode requires a trusted identity; no implicit enrollment.
- Implement the trust record in Section 5.3, or require the caller (ProjectStore/CLI) to supply the identity from a trusted source. Tests may inject a temp identity directly.
- Add tests: missing trust record -> `E_ROOT_UNTRUSTED`; existing record with changed identity -> `E_ROOT_CHANGED`; record with same identity but moved path -> `E_ROOT_MISSING`; explicit re-enrollment requires the confirmation flag; trust record ACL failure -> refuse.
- A production write path must not fall back to "open and trust" if the record is missing.

#### R-13 - Public errors are not exhaustively mapped (`P0`)

Current: `writer.py` has `except OSError` fallbacks in some places but not all; Unicode/ctypes/OSError can escape, and a raw exception can bypass the structured result. The audit recorded `UnicodeEncodeError` escaping `create`.

Required:

- Every public method wraps the operation in a boundary that converts `SafeWriterError`, `OSError`, `UnicodeError`, `ValueError`, and unexpected exceptions into a structured result; unexpected exceptions are `RECOVERY_REQUIRED` if a side effect may have occurred, else `FAILED`.
- Do not catch `KeyboardInterrupt`/`SystemExit` and continue writing. Let them terminate; the journal/lock are closed by the OS. Document that a Ctrl-C during a side effect is a crash-like event.
- Add a test that injects each of `OSError`, `UnicodeError`, `ValueError`, and a generic `RuntimeError` at each public boundary and asserts a typed code, no silent success, and no handle leak.

### 8.2 P1 remediation

#### R-14 - Unicode validation is incomplete (`P1`)

2.4 audit: `validate_simple_name` accepted an unpaired surrogate; `native._us` (`native.py:266`) raised `UnicodeEncodeError`; `journal.append` (`journal.py:154`) raised while encoding. The audit reproduced escapes through `probe_absent` and `_ensure_create_parent`.

2.4R status: **CLOSED.** `policy.py:46-55` defines `_has_surrogate`, with checks in `validate_simple_name`, `validate_ref`, `validate_reason`, and `validate_root_path` (`:72`, `:101`, `:113`, `:146`). `journal.py:155-164` maps a Unicode encode failure to `SafeWriterError(E_PATH_INVALID)` before writing. Independent probe: a lone high surrogate in a target component and in a parent component is refused with `E_PATH_INVALID`; non-BMP valid names still round-trip. Tests: `tests/test_creation_safewriter.py:176`, `:185`, `:194`.

Required at admission:

- Keep strict UTF-16-LE and UTF-8 encoding checks for every relative path, parent component, and temp name before any native/journal call; map failure to `E_UNICODE` (or the documented `E_PATH_INVALID`) consistently.
- Keep tests for a lone high surrogate, a lone low surrogate, a reversed pair, a non-BMP character (valid, must round-trip), and a name that becomes invalid only after appending a suffix.
- The journal must encode with `ensure_ascii=False` and reject surrogates before writing; a record that cannot be encoded is a refusal, not a partial append.

#### R-15 - validate_root_path` normalizes before checking `..` (`P1`)

Current (2.4R): `policy.py:155` calls `ntpath.normpath`, then `:167` checks components. `C:\foo\..\bar` becomes `C:\bar` and passes, contradicting the docstring.

Required:

- Split the raw path into components **before** normalization; reject any `.` or `..` component; then normalize only for a canonical display/path string.
- Reject `C:foo` (drive-relative), `\\?\`/`\\.\` device paths, UNC, and ADS in the root path unless explicitly supported. For v1, support only `X:\...` absolute drive paths.
- Add tests for `..`, `.`, mixed separators, trailing separators, trailing dots/spaces, `C:foo`, `\\server\share`, `\\?\C:\...`, `\\.\C:\...`, and a path with a colon in a component.
- Keep the `VolumeFacts` check after validation; do not call native on an invalid path.

#### R-16 - validate_simple_name` misses Windows name hazards (`P1`)

2.4 audit: separators and some names were rejected, but reserved devices, ADS, control characters, and trailing dot/space were not proven.

2.4R status: **PARTIAL.** `policy.py` now rejects `:`, `/`, `\`, NUL, control characters (`0x01-0x1F`), empty names, `.`/`..`, trailing dot/space, reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, with or without an extension), unpaired surrogates, and components over its documented 200 UTF-16 code-unit bound. Independent probe: `a\x01b`, `x.`, `x `, `CON`, `con.txt`, `a:b`, and a 256-character component were refused; `COM0` and `LPT10` are not reserved and were accepted. There is no table-driven regression for these hazards.

Required at admission:

- Add a table-driven test for every hazard, including case variants (`con`, `Con.txt`, `COM1`, `lpt9.log`), ADS syntax (`a:b`), control characters, trailing dot/space, and the length bound.
- Keep the documented length bound and extended-path behavior consistent; refuse a component that exceeds it rather than truncating.
- Do not "sanitize" a name by replacing characters; refuse. Silent renaming changes the user's intended path.

#### R-17 - Test suite false positives, duplicates, and missing mutation coverage (`P1`)

2.4 audit:

- `test_temp_write_oserror_returns_structured_failure` patched `writer.native.write_all`; the first call was the lock owner metadata at `lock.py:129`, so the test failed at lock acquisition and never created a temp. The assertion "no `.veyra-*.tmp`" passed for the wrong reason.
- `test_non_bmp_root_parent_and_target_are_not_truncated` was defined twice (`:127`, `:142`); the second overwrote the first. 21 `def test_` names, 20 methods, 19 unique behaviours.
- Disabling `_verify_binding` still passed all 20 tests.

2.4R status: **PARTIAL.** The temp test now triggers only on the payload write, proves the temp existed, and asserts cleanup (`tests/test_creation_safewriter.py:254`); the duplicate is gone; the focused suite has **26 unique tests** and includes handle-leak and surrogate tests. **Still open:** a 2.5 mutation run with `_verify_binding` replaced by a no-op passes all 26 tests; no test covers a hardlink created after open, replace compensation, an internal-anchor swap, or a dedicated `root.verify()`/`PARENT_READY` fault.

Required at admission:

- Add mutation tests that must fail when each P0 check is disabled: binding, reparse, nlink, no-replace, journal-before-side-effect, lock, rollback, root identity.
- Add tests for a hardlink created after open, each internal anchor swapped in turn, replace compensation, and fault hooks at `root.verify()` and `PARENT_READY`.
- Keep the handle-leak tests and extend them to every new path; require zero growth over N iterations after warm-up.
- Add a test that the suite writes only under `tempfile.TemporaryDirectory`, never into the repository root.
- Report the current count as **26 focused / 353 full tests**. The 2.4 report's `:7` "20 tests / 347 / dual Python" is historical, not a production admission result.

#### R-18 - ProjectStore writes bypass the SafeWriter boundary (`P1`, P0 for integration)

Current: `ProjectStore._write_json()` uses `write_json_atomic(path, payload)` and `decisions_path.open("a")`; these are path-based and not handle-bound. A strict SafeWriter that only protects `create/replace` while ProjectStore continues to write state files by path leaves the main attack surface open.

Required:

- Do not claim production admission until every project-content write goes through the strict boundary or a documented read-only exception.
- Inventory all `ProjectStore` writes: state, spec, plan, decisions, locks, placeholders, digest, runs; classify each as project content, metadata, or append-only log.
- For each write, either (a) route through SafeWriter with an anchored target and journal, or (b) prove it is outside the protected project root (for example, a user-profile cache) and document it.
- Do not migrate in 2.5; this is the integration gate in Section 10.4.
- Add an integration test that attempts to replace `creation/state.json` during a SafeWriter operation and verifies the ProjectStore path cannot be redirected.

#### R-19 - Resource limits and observability (`P1`)

Current: no explicit journal size/record limit beyond 64 KiB line; no temp quota; no handle quota; no operation timeout; handle leaks are invisible until process exhaustion.

Required:

- Journal max (8 MiB / 100,000 records), temp max count/size, quarantine max count, operation timeout (a watchdog is advisory; the journal is the source of truth).
- Structured logging with operation ID, component, identity, and code; no secrets, no file contents.
- Metrics for refused operations by code, recovery-required count, handle count, and duration. Do not log raw paths at info level if the project path is sensitive; log the relative path and root hash.
- On resource exhaustion, refuse before side effect; do not delete old quarantine to make room.

#### R-20 - Protected-file policy is not a first-class invariant (`P1`)

Current: tests assert the demo/protected files are untouched, but there is no API-level protected set; a caller can pass `report.md` as a target.

Required:

- `SafeWriter` accepts a `protected_relpaths` set from the caller (ProjectStore/CLI) and refuses create/replace/delete of a protected path before opening the target.
- The default protected set includes `report.md`, `real_crash.log`, and any caller-declared user files.
- Add tests: direct target, parent directory that contains a protected file, case variant, trailing-dot variant, and a hardlink alias to a protected file (nlink>1 refusal covers the last one; test it).
- This is defense in depth; the user's instruction "never touch report.md" is also enforced by tests and by the CLI's path allowlist.

### 8.3 P2 remediation

- **R-21 - Documentation corrections.** Fix the 2.4 report's root-rename attribution (`:204`) and the inaccurate helper suggestion (`:327`); state that the blocker was a live descendant file handle. Mark the 2.4 report as historical, not an admission artifact.
- **R-22 - Duplicate test name.** Remove the duplicate non-BMP test; keep one strong round-trip test plus a sibling test.
- **R-23 - Journal docstring.** Replace "corruption" with the exact v2 tail-repair rule; link to Section 7.5.4.
- **R-24 - README/status wording.** Do not describe SafeWriter as production-ready. The current README status text is outside this design's scope but must not be updated to claim admission.
- **R-25 - Test naming.** Rename tests to say what invariant they prove (`test_binding_detects_swapped_creation_dir`, not `test_..._works`).
- **R-26 - Cross-version.** Record the Python/Windows matrix; do not claim 3.14 results that were not re-run at the baseline.
- **R-27 - Sample/protected files.** Ensure no test fixture writes to repository root; the audit's leaked `.veyra-*.tmp` is evidence that temp placement matters.

### 8.4 Remediation order

1. R-05 (temp staging/nlink) and R-07 (directory shares) - These change the filesystem behavior every later test observes.
2. R-06/R-11 (binding all anchors) - The core identity invariant.
3. R-10 (journal v2) - Framing, tail policy, and crash classification.
4. R-08 (replace compensation) - Depends on the journal and binding.
5. R-12 (root trust) and R-13 (error boundary) - Public contract.
6. R-15..R-17 and R-19 (validation, mutation tests, limits) - Parallel after the core; R-14 is closed and retained as a regression.
7. R-20 (protected policy) and ProjectStore integration - Before any production claim.

R-01..R-04 are already closed in 2.4R; do not re-implement them, but keep their regression tests. Do not start with an integration; a broken prototype integrated into ProjectStore spreads the defect.

### 8.5 P1 ownership and closure

No P1 item is "done" because it has a paragraph. Each item below has an owner phase, evidence, and a closure condition. `Owner` means the phase that must produce the evidence; the independent auditor in 2.9 still verifies it.

| ID | Owner | Target phase | Required evidence | Closure condition |
|---|---|---|---|---|
| R-19 Resource limits and observability | implementation owner | 2.6 | T-53; configured limits; refusal tests at exact max/max+1; structured log fields; no secrets | Limits are enforced before side effects; `E_JOURNAL_FULL`/`E_LIMIT` behavior is tested; observability fields are present in every result/log; no cleanup-by-deletion on exhaustion |
| R-20 Protected-file policy | SafeWriter API owner + ProjectStore owner | 2.6 API, 2.8 integration | T-54; API-level `protected_relpaths`; direct/case/trailing-dot/hardlink-alias tests | Protected target is refused before opening the target; default set includes `report.md`/`real_crash.log`; enforcement is at the API boundary, not only CLI |
| R-21 Documentation corrections | design/doc owner | 2.5R.1 design + 2.8 report | corrected 2.4 report attribution; design Section 8.3 cross-reference; no production claim | The root-rename blocker is attributed to the live descendant handle; the inaccurate helper suggestion is corrected; 2.4 report is marked historical |
| R-22 Duplicate test name | test owner | 2.6 | one non-BMP round-trip test + one sibling test; no duplicate `def test_` | Duplicate is gone; both behaviours are independently asserted |
| R-23 Journal docstring | implementation owner | 2.6 | docstring matches Section 7.5.4 (torn-tail vs complete-invalid; atomic repair) | No "corruption" wording that contradicts the v2 rule |
| R-24 README/status wording | release owner | 3.0 only | status text names exact supported scope; no admission before 2.9 | No production-ready claim before 3.0; 2.6/2.7/2.8 do not edit README to claim admission |
| R-25 Test naming | test owner | 2.6 | names state the invariant (e.g. `test_binding_detects_swapped_creation_dir`) | Renamed tests still fail under the matching mutation |
| R-26 Cross-version evidence | test owner | 2.6 | exact Python/Windows matrix rerun at the admission revision; raw output | No 3.14/3.15 claim that was not rerun; skipped tests are listed, not hidden |
| R-27 Sample/protected fixtures | test owner | 2.6/2.8 | all fixtures under `TemporaryDirectory`; no repository-root temp; leak check | No test writes to the repository root; leaked temp is a test failure |

R-21 and R-24 are documentation/release items; they do not authorize code changes in 2.5R.1. R-22/R-23/R-25/R-26/R-27 are 2.6 test/docstring work. R-19's limits are 2.6 implementation. R-20's API is 2.6; its ProjectStore enforcement is 2.8.


---

## 9. Test and verification matrix

### 9.1 What a test must prove

A test is admissible only if it:

1. Fails when the invariant is broken (mutation check).
2. Uses a temporary root created by the test, never the repository.
3. Records the exact OS, Python, filesystem, identity, and code revision.
4. Asserts both the result status **and** the filesystem state (target, temp, quarantine, journal).
5. Asserts handle count returns to the pre-test baseline under the exact procedure in Section 9.1.1. A one-off "delta <= 4" observation is diagnostic, not a pass.
6. Does not rely on a timing race that is not synchronized by a hook; use fault hooks at named phases.
7. Distinguishes `SKIP` from `PASS`. A test skipped because the environment lacks symlink privilege is `UNVERIFIED`, not green.

#### 9.1.1 Handle-leak admission procedure

The handle criterion is deliberately mechanical because the earlier "within a documented warm-up" wording was not falsifiable. For each path under test:

1. **Warm-up:** run the operation 10 times on a fresh temporary root. Discard these observations.
2. **Baseline:** after the warm-up, wait 250 ms, then take five `GetProcessHandleCount` samples 100 ms apart. All five must be identical. If they are not, the environment is noisy: report `UNVERIFIED_ENV_NOISE`, do not mark PASS, and do not "stabilize" by choosing the lowest sample.
3. **Measure:** run the operation 100 times on the same root. Record the handle count after each iteration.
4. **Quiesce:** wait 500 ms, then take five samples 100 ms apart. All five must equal the baseline.
5. **Leak condition:** the test FAILS if the final quiescent count is greater than the baseline, or if any post-iteration sample exceeds baseline + 1 for two consecutive iterations. A transient +1 that returns by the next iteration is allowed; a final +1 is a leak, not noise.
6. **Per-path isolation:** run the procedure separately for the known leak paths (root identity mismatch, `open_path` missing component, temp write failure, parent-is-file) and for at least one newly created path. Each path gets its own baseline; a pass on one path does not cover another.
7. **Evidence:** emit the baseline, all samples, the environment, and the exact operation. A test that cannot emit these is not admission-grade.

The 25-iteration runs recorded in Section 9.3 are **diagnostic only**. They can show gross growth, but they cannot establish the admission criterion, and no gate may cite them as a pass. A delta of 4 is already a failure under this procedure, not "within tolerance".

### 9.2 Test levels

| Level | Scope | Examples |
|---|---|---|
| L0 - Pure policy | no I/O | names, paths, volumes, protected set, error mapping |
| L1 - Native wrappers | one API per test, temp root | identity, reparse, rename flags, lock, flush, struct sizes |
| L2 - Anchors | root chain, child open, binding | ancestor swap, reparse at each level, expected mismatch, leaks |
| L3 - Journal/lock | append/recover, contention | torn tail, hash chain, stale lock, deletion attempt |
| L4 - Operation state machine | create/replace/rollback | fault hook at each event, postcondition failure, compensation |
| L5 - Adversarial integration | multi-process/race | hardlink, junction, concurrent writer, AV-like handles |
| L6 - Crash/power loss | kill/power cut | VM snapshot at each transition |
| L7 - ProjectStore/CLI | end-to-end | strict mode, protected files, no path-based writes |

### 9.3 Adversarial matrix

Statuses reflect the current 2.4R tree, not the 2.4 audit. `PASS` means an executed regression exists in the 26-test focused suite; `PARTIAL` means the code is fixed but a dedicated test is missing; `FAIL` means the behavior is still open; `UNVERIFIED` means no admissible test on this machine.

| ID | Attack / condition | Expected result | Current status |
|---|---|---|---|
| T-01 | Expected root identity mismatch | `E_ROOT_CHANGED`; zero handles; no side effect | **PARTIAL** (`:134`; 25-iteration diagnostic, 0 delta; 100-iteration Section 9.1.1 not run) |
| T-02 | Missing component in `open_path` | `E_TARGET_MISSING`/structured; zero handles | **PARTIAL** (`:147`; 25-iteration diagnostic, 0 delta; 100-iteration Section 9.1.1 not run) |
| T-03 | `root.verify()` failure after open | structured; zero handles | **PARTIAL** (code fixed; dedicated fault test missing) |
| T-04 | `PARENT_READY` journal append failure | structured; zero handles; no target | **PARTIAL** (code fixed; dedicated fault test missing) |
| T-05 | Temp second-write failure | temp cleaned or recovery; no orphan | **PASS** (`:254`; no temp remains) |
| T-06 | Temp nlink > 1 at each checkpoint | `E_HARDLINK_REFUSED`/recovery; no success | **FAIL** (no pre-commit check) |
| T-07 | Target pre-existing hardlink | refuse before modify | **PASS** (target open nlink check; add race test) |
| T-08 | Hardlink created after open | detected at next checkpoint; not silent | **FAIL** (no checkpoint) |
| T-09 | Root/parent rename while open | blocked by no-delete dirs | **FAIL** (share ALL); design requires test |
| T-10 | `creation` replaced after session open | `E_BINDING_CHANGED`; no write | **FAIL** (not revalidated) |
| T-11 | Journal/lock replaced after open | `E_BINDING_CHANGED`; no append/unlock | **FAIL** (not revalidated) |
| T-12 | Disable `_verify_binding` | test suite fails | **FAIL** (no-op passes all 26) |
| T-13 | Reparse at root component | `E_REPARSE_REFUSED`; no follow | **PARTIAL** (junction PASS; symlink UNVERIFIED) |
| T-14 | Reparse at target | `E_REPARSE_REFUSED`; no follow | **PARTIAL** (junction PASS; symlink UNVERIFIED) |
| T-15 | Junction target redirect | no write through target | **PASS** (no-follow open) |
| T-16 | Symlink target redirect | no write through target | **UNVERIFIED** (no privilege) |
| T-17 | Mount point / unknown reparse | refuse | **UNVERIFIED** (tag logic; add test) |
| T-18 | Create collision | `E_TARGET_EXISTS`; no overwrite | **PASS** (no-replace) |
| T-19 | Replace target changed before commit | refuse/rollback; no overwrite | **PARTIAL** (identity check PASS; race UNVERIFIED) |
| T-20 | Replace final rename failure | safe rollback or recovery; no silent success | **FAIL** (always recovery) |
| T-21 | Quarantine identity changed | no rollback; recovery | **UNVERIFIED** |
| T-22 | Rollback rename fails | recovery; preserve both | **UNVERIFIED** |
| T-23 | Lock same-process second writer | `E_LOCK_BUSY` | **PARTIAL** (lock call PASS; public test missing) |
| T-24 | Lock cross-process second writer | `E_LOCK_BUSY` | **PARTIAL** (lock call PASS; public test missing) |
| T-25 | Lock file delete/rename while held | sharing violation | **PARTIAL** (observed; test missing) |
| T-26 | Stale lock file after crash | acquire succeeds | **UNVERIFIED** (component test) |
| T-27 | Torn journal tail | repair + record | **PARTIAL** (code; test missing for all cases) |
| T-28 | Complete-but-invalid journal line | refuse; no truncate | **UNVERIFIED** |
| T-29 | Journal full/oversize/disk full | refuse before side effect | **UNVERIFIED** |
| T-30 | Unicode lone surrogate | `E_UNICODE`; no escape | **PASS** (`:176`, `:185`, `:194`) |
| T-31 | Root path `..`/`.` | refuse | **FAIL** (normalized away) |
| T-32 | ADS/reserved/trailing dot | refuse | **PARTIAL** (policy rejects; table-driven test missing) |
| T-33 | Non-BMP valid name | preserved exactly | **PASS** (duplicate removed; `:223`) |
| T-34 | Case collision | `E_TARGET_EXISTS` | **UNVERIFIED** |
| T-35 | Long path > MAX_PATH | supported or refused deterministically | **UNVERIFIED** |
| T-36 | Non-NTFS/removable/network | `E_UNSUPPORTED_VOLUME` | **PARTIAL** (volume facts; coverage unclear) |
| T-37 | Protected file target | refuse | **FAIL** (no API set) |
| T-38 | **Process crash** before/after each journal event and each content side effect | exactly one recovery classification; no silent success; no lost content | **NOT TESTED** (recovery not implemented) |
| T-39 | **Power loss** at each transition | target/journal/quarantine matches one allowed state; durability claim limited to what the VM test proves | **BLOCKED** (disposable VM unavailable; T-38 process kill is not a substitute) |
| T-40 | Concurrent writer ignoring lock | this process never follows; other writer may succeed | **UNVERIFIED** (INFO: cooperative limit) |
| T-41 | AV opens target read-only | operation still works | **UNVERIFIED** |
| T-42 | AV opens target write/delete | fail closed | **UNVERIFIED** |
| T-43 | Open handle leak under Section 9.1.1 | final quiescent count == baseline | **FAIL** (diagnostic 25 iterations showed delta <= 4; 100-iteration procedure and new-path coverage not run) |
| T-44 | Public error boundary | typed result, no escape | **PARTIAL** (Unicode closed; generic OSError/ctypes boundary not exhaustive) |
| T-45 | ProjectStore writes by path | no production claim | **FAIL** (not integrated) |
| T-46 | Trust schema version unsupported | `E_TRUST_SCHEMA_UNSUPPORTED`; no write | **UNVERIFIED** (design only; no trust store implementation) |
| T-47 | Trust record digest/schema/duplicate corruption | `E_TRUST_RECORD_CORRUPT`; no auto-repair | **UNVERIFIED** (design only) |
| T-48 | Trust record copied to another user / unsafe ACL | `E_TRUST_RECORD_FOREIGN`/`E_TRUST_ACL_UNSAFE`; refuse | **UNVERIFIED** (design only) |
| T-49 | Root moved, recreated, or volume changed | `E_ROOT_MOVED`/`E_ROOT_CHANGED`/`E_VOLUME_CHANGED`; no auto-update | **UNVERIFIED** (design only) |
| T-50 | Older valid trust record restored (rollback) | accepted only if identity matches; no rollback-detection claim | **UNVERIFIED** (design only; residual documented) |
| T-51 | Crash between trust-record backup and temp rename | `E_TRUST_RECORD_MISSING`; no auto-adopt | **UNVERIFIED** (design only) |
| T-52 | Trust record ACL changed after enrollment | `E_TRUST_ACL_CHANGED`; explicit re-enrollment | **UNVERIFIED** (design only) |
| T-53 | Create recovery CR-1..CR-10, including parent partial, orphan temp, `COMMIT_INTENT` + target absent + temp intact, target new without `COMMITTED`, mismatch, temp+target, neither object, append/flush failure, binding change | exactly one classification; only the Section 7.7.2 action; no automatic content change | **NOT TESTED** (2.7) |
| T-54 | Protected-file API: direct, case variant, trailing-dot variant, hardlink alias, protected parent directory | refused before opening the target; no write/rename/delete | **NOT TESTED** (2.6 API, 2.8 integration) |
| T-55 | Journal capacity at exact `MAX_RECORD_BYTES`/`MAX_JOURNAL_BYTES`/`MAX_RECORDS` and each +1 | exact max accepted; +1 refused before side effect; no truncation | **NOT TESTED** (2.6) |
| T-56 | Journal archive handoff: archive verify, metadata, chain handoff, crash during archive | source preserved until archive verifies; new chain starts with handoff; no silent rotation | **NOT TESTED** (future; not implemented in 2.5R.1) |
| T-57 | Evidence JSONL schema: required fields, one object per line, hashes, handle samples | schema-valid; a bare `handle_delta:0` is rejected | **NOT TESTED** (2.6) |
| T-58 | Handle baseline/quiescence on every new path (Section 9.1.1) | final quiescent count == baseline; no monotonic growth | **NOT TESTED** (2.6; current 25-iteration diagnostic is not admission evidence) |
| T-59 | Binding each component separately: root, ancestors, `creation`, `operations`, journal, lock, quarantine, parent, target | `E_BINDING_CHANGED` for each swap; no side effect after detection | **NOT TESTED** (2.6; T-12 proves the current gap) |
| T-60 | Replace compensation C1-, including `ROLLBACK_INTENT` append failure, rename failure, `ROLLED_BACK` append failure, corrupt journal, binding change, `COMMITTED` present | C1 no rollback attempt; C2/C3 recovery; C6 no rollback; no automatic retry | **NOT TESTED** (2.7) |
| T-61 | Public API exception boundary: every thrown exception, ctypes failure, Unicode error, OSError, and status mapping | typed result; no raw exception; truthful side-effect state | **NOT TESTED** (2.6) |

### 9.4 Current test-suite facts

At the 2.4R baseline, rechecked in 2.5:

- Python 3.15.0: `python -m unittest discover -s tests` -> **353 tests, OK (skipped=4)** in 8.691 s, exit 0.
- Python 3.14.7 with `PYTHONUTF8=1` (`<python-3.14-msys2>`): **353 tests, OK (skipped=4)** in 9.317 s, exit 0.
- `python -m unittest tests.test_creation_safewriter`: **26 tests, OK**.
- The SafeWriter source has 26 `def test_` names, 26 unique behaviours, no duplicate.
- The focused suite now catches the root-chain, `open_path`, parent-file, temp-orphan, and surrogate regressions (tests at `:134`, `:147`, `:164`, `:176`, `:185`, `:194`, `:254`).
- A no-op `_verify_binding` still passes all 26 tests (`Ran 26 tests ... OK`, 2.5 mutation run).
- Still not covered: a dedicated `root.verify()` or `PARENT_READY` fault, a hardlink created after open, an internal-anchor swap, replace compensation, crash recovery.

Therefore the current suite is a **prototype regression suite**, not an admission suite. It is stronger than the 2.4 suite, but it still does not fail when every P0 invariant is broken.

### 9.5 Fault-injection design

Use a test-only `FaultPlan` passed to `SafeWriter`:

```
FaultPlan:
  at: "before|after"
  phase: "TEMP_WRITE" | "PARENT_READY" | "COMMIT_RENAME" | ...
  action: "raise_oserror" | "raise_unicode" | "swap_creation" | "add_hardlink" | ...
  once: bool
```

Rules:

- Fault hooks are compiled out of production or gated by a private constructor; they must not be reachable from the public CLI.
- Each hook runs **inside** the operation's handle context, at a named point, so the test can mutate the filesystem while the session is live.
- A hook that mutates a path must use an independent handle/process; it must not use the SafeWriter's own file operations.
- After a hook raises, the test asserts the journal's last durable event, the target/quarantine/temp identities, the handle count, and the returned code.
- Tests for the race window use a barrier/latch, not `sleep`.

### 9.6 Crash and power-loss testing

These are two different claims and two different tests. A process crash leaves the OS cache intact; it does not prove `FlushFileBuffers`, directory-entry persistence, or recovery after a power cut. A power-loss test does not replace the crash matrix. `T-38` is the process-crash test; `T-39` is the power-loss test.

#### 9.6.1 Process-crash verification (P0-11, T-38)

- A child process runs a create/replace with a `FaultPlan` hook that calls `os._exit(1)` immediately **before** and **after** every journal event and every content side effect. The hook runs inside the operation's handle context.
- The parent waits for exit, then runs `recover --diagnose` on the same root; it asserts exactly one classification from Section 7.6, the last durable event, target/quarantine/temp identities, and that no content changed except the side effect the state proves.
- Repeat with `TerminateProcess` (no Python cleanup) to test handle release and lock release.
- Repeat with the journal handle closed by the kill and with the journal at `MAX_RECORDS - 1`, so the terminal append is the failing step.
- The test must prove that no `SUCCEEDED` result is returned for a state whose terminal record is absent, and that no rollback is attempted without a durable `ROLLBACK_INTENT`.
- Artifact: T-57 evidence JSONL with the child exit code, fault point, classification, last event, identities, and journal hash.

A process-crash PASS means "the on-disk state is classifiable and the documented recovery action is safe under process termination." It does **not** mean "power-loss safe".

#### 9.6.2 Power-loss verification (T-39)

- Requires a disposable Windows VM, a snapshot/revert mechanism, and a way to remove power at a disk-flush boundary. A process kill, `TerminateProcess`, or container stop is not a power-loss test.
- At minimum: snapshot before each transition; force power off at the boundary; reboot; run read-only diagnosis; record the target/journal/quarantine state and whether it matches an allowed classification.
- If the VM or power control is unavailable, the correct status is **`BLOCKED`**, not `PASS`, `PARTIAL`, or "passed by inspection". State the unverified claim exactly: "journal append is flushed before the side effect; persistence of the directory entry and of the journal tail across sudden power loss is **unverified**."
- Do not use `FlushFileBuffers` success as proof of power-loss durability. It is a necessary API call, not an empirical result.

#### 9.6.3 Gate mapping

| Claim | Evidence | Gate effect |
|---|---|---|
| Intent event is appended and flushed before the content side effect; process kill after the append does not lose the intent in the OS-visible state | T-38 fault points + append instrumentation | `P0-10` may PASS for this claim |
| A process crash at any transition is classifiable and recoverable per Section 7.7 | T-38, all Section 7.6 rows | `P0-11` may PASS |
| Journal directory entry and tail survive sudden power loss | T-39 VM run | If absent: `T-39=BLOCKED`; **no power-loss claim**; `P0-10`/`P0-11` do not include it |
| Recovery after power loss | T-39 VM run | If absent: not claimed; 2.7 must not say "power-loss safe" |

The roadmap must choose one of two honest positions:

1. **Process-crash scope:** 2.7 exits with P0-11 PASS for process crash; 3.0 admits with the explicit limitation "process-crash recovery; power-loss durability unverified." This is allowed only if the public wording and `durability_claim` repeat the limitation.
2. **Power-loss scope:** 3.0 is blocked until T-39 runs on a disposable VM and passes. This is the stronger claim; it is not required merely to ship the process-crash-safe feature.

`durability_claim=DIRECTORY_FLUSHED` may be reported only with `power_loss_tested=false`; it must never be rendered as "power-loss safe." `durability_claim=UNVERIFIED` is the default when the directory flush was not observed.


### 9.7 Test evidence format

The evidence artifact is **JSONL**, not a prose report and not a single aggregate JSON. Each line is one test invocation. A run directory contains:

```
evidence/<run_id>/
  run.json                    # run-level metadata, one object
  tests.jsonl                 # one test record per line
  artifacts/<test_id>/...     # journal copies, fault logs, screenshots
```

`tests.jsonl` framing:

- UTF-8 without BOM; LF line endings; one JSON object per line; no blank lines.
- A line is at most 1 MiB; a larger line is invalid.
- Strict JSON only: no `NaN`, `Infinity`, comments, or duplicate keys.
- Each line is independently parseable; a truncated final line is invalid, not "best effort".
- The file is append-only during a run; a rerun writes a new `run_id`, never appends to an old run.
- `run.json` records `run_id`, `source_revision`, `dirty`, `tool_version`, `schema_version`, `started_at`, `finished_at`, `host`, `environment_id`.

Required test record fields:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | evidence schema version, e.g. `"1.0"` |
| `test_id` | string | `T-20`, `T-53`, etc. |
| `gate_id` | string/array | `P0-08`, `P0-11`, or `null` for non-gate tests |
| `test_level` | string | `L0`..`L7` from Section 9.2 |
| `source_revision` | string | exact commit hash tested |
| `dirty` | boolean | working tree had uncommitted changes |
| `environment` | object | `os_build`, `python`, `filesystem`, `volume_serial_hash`, `arch` |
| `root` | string | temporary root; may be redacted, but the identity hash is required |
| `status` | string | `PASS`, `FAIL`, `PARTIAL`, `BLOCKED`, `NOT TESTED` |
| `expected_result` | string | the exact expected status/classification |
| `observed_result` | string | the actual status/classification |
| `error_code` | string/null | public error code |
| `operation_status` | string/null | `SUCCEEDED`, `REFUSED`, etc. |
| `journal_state` | object | `path`, `sha256`, `size`, `records`, `last_event`, `last_seq`, `terminal`, `tail_repaired` |
| `objects` | array | each `{role, relpath, identity, hash, nlink, exists}` for target/temp/quarantine/created dirs |
| `failure_injection` | object/null | `phase`, `action`, `once`, `process`, `return_code` |
| `assertions` | array | each `{name, expected, observed, pass}`; no assertion is implied |
| `handle` | object/null | Section 9.7.1 samples for handle tests |
| `durability_claim` | string | `FLUSHED`, `DIRECTORY_FLUSHED`, `UNVERIFIED` |
| `power_loss_tested` | boolean | true only for T-39 on a VM |
| `recovery_classification` | string/null | Section 7.6 classification |
| `artifacts` | array | each `{path, sha256, size}` relative to the run directory |
| `duration_ms` | integer | wall time |
| `started_at` / `finished_at` | string | ISO-8601 UTC |
| `notes` | string | limitations, skipped steps, owner decision reference |

`status=PASS` requires: every assertion true, the observed result equal to expected, at least one artifact with a hash, and no skipped required step. A test that cannot emit these fields is not admission-grade. A test that ran but was skipped is `BLOCKED` or `NOT TESTED`, never `PASS`.

#### 9.7.1 Handle evidence

A bare `handle_delta: 0` is not evidence. Handle tests emit the full Section 9.1.1 sample set:

```json
{
  "handle": {
    "warmup_iterations": 10,
    "baseline_samples": [101, 101, 101, 101, 101],
    "measured_samples": [101, 101, 101, 101, 101],
    "quiescent_samples": [101, 101, 101, 101, 101],
    "baseline_median": 101,
    "measured_median": 101,
    "final_delta": 0,
    "slope": 0.0,
    "noise_classification": "stable",
    "sample_interval_ms": 250
  }
}
```

Rules:

- 10 warm-up iterations, 5 baseline samples, 100 measured iterations, 5 quiescent samples.
- `measured_samples` has 100 entries (or one entry per iteration if sampled less often, with `sample_interval_ms` declared); the raw list is required.
- `noise_classification` is `stable`, `noise`, `leak`, or `inconclusive`. Only `stable` with `final_delta=0` and `slope<=0` supports a no-leak claim.
- A 25-iteration diagnostic is labeled `diagnostic_only=true` and cannot be used for admission.
- The evidence must name the path exercised and whether it is a new path; a handle test on only the happy path does not cover error paths.

#### 9.7.2 Example (abbreviated)

```json
{"schema_version":"1.0","test_id":"T-20","gate_id":"P0-08","test_level":"L4","source_revision":"88c5107","dirty":true,"environment":{"os_build":"Windows 11 10.0.26300","python":"3.15.0","filesystem":"NTFS","volume_serial_hash":"...","arch":"x64"},"root":"<temp>","status":"PASS","expected_result":"safe rollback or recovery; no silent success","observed_result":"RECOVERY_REQUIRED; target absent; quarantine old","error_code":"E_COMMIT_UNCERTAIN","operation_status":"COMMIT_UNCERTAIN","journal_state":{"path":"creation/operations/<batch>.journal.ndjson","sha256":"...","size":1234,"records":9,"last_event":"COMMIT_INTENT","last_seq":9,"terminal":false,"tail_repaired":false},"objects":[{"role":"target","relpath":"a.txt","identity":"...","hash":null,"nlink":0,"exists":false},{"role":"quarantine","relpath":"creation/quarantine/<batch>/a.txt","identity":"...","hash":"...","nlink":1,"exists":true}],"failure_injection":{"phase":"COMMIT_RENAME","action":"raise_oserror","once":true,"process":"child","return_code":1},"assertions":[{"name":"target_absent","expected":true,"observed":true,"pass":true},{"name":"quarantine_preserved","expected":true,"observed":true,"pass":true}],"handle":null,"durability_claim":"FLUSHED","power_loss_tested":false,"recovery_classification":"SAFE_ROLLBACK","artifacts":[{"path":"artifacts/T-20/journal.ndjson","sha256":"...","size":1234}],"duration_ms":42,"started_at":"2026-11-22T00:00:00Z","finished_at":"2026-11-22T00:00:00Z","notes":"process crash, not power loss"}
```


### 9.8 Admission checklist

Admission is gate-based, not phase-based. A gate is **owned** by a phase, but ownership is not a waiver: a gate opened in 2.6 that is not closed until 2.9 still blocks 3.0. The same-user hardlink race is split into an implementation gate (`P0-04a`) and a decision gate (`P0-04b`) so that an accepted residual cannot be mistaken for a fixed race.

#### 9.8.1 Status vocabulary

| Status | Meaning |
|---|---|
| `PASS` | The named admission test executed on an admitted environment and failed when the invariant was mutated. Evidence attached. |
| `PASS WITH ACCEPTED RESIDUAL` | Only `P0-04b`. The residual is explicitly accepted by the owner under D-01; the race is **not** fixed. This status is a recorded exception, not a PASS, and cannot be used for any other gate. |
| `PARTIAL` | Some component behavior or diagnostic evidence exists; no admission-grade test/evidence. |
| `OPEN` | Not implemented, or implemented without the required test. |
| `NOT STARTED` | The activity has not begun. |
| `NOT TESTED` | A test specification exists but no admissible execution exists; not a pass. |
| `BLOCKED` | The test cannot be executed in the current environment (VM, privilege, hardware); the claim remains unverified. |
| `EXCLUDED` | Deliberately outside the production surface; the exclusion and its consequences are documented. It is not a PASS. |
| `DEFERRED` | Deliberately moved to a named later phase; it still blocks 3.0. |

`UNVERIFIED` in Section 9.3 is a legacy label; new rows use `NOT TESTED` or `BLOCKED` and state which. `BLOCKED` is not a synonym for PASS or for "accepted residual".

#### 9.8.2 P0 gates and phase ownership

| Gate | Requirement | Owning phase | Current |
|---|---|---|---|
| P0-01 | Threat model and trust assumptions approved; B1/B2/B3/B4 boundary explicit; no claim that SafeWriter protects against B2 | 2.5 review + 2.9 sign-off | **OPEN** (this document; needs owner/security sign-off) |
| P0-02 | Root trust record schema v2, owner-only ACL, explicit enrollment, no implicit first-use enrollment; D-11 limitation recorded | 2.6 | **OPEN** (design only in Section 5.3) |
| P0-03 | All reparse points refused at every component; symlink test on a capable environment | 2.6 | **OPEN** (junction PASS; symlink T-16 BLOCKED) |
| P0-04a | Target/temp/quarantine nlink checks at every checkpoint; hardlink created after open detected before the next side effect | 2.6 | **OPEN** (only target pre-open check exists) |
| P0-04b | Same-user hardlink race eliminated **or** explicitly owner-accepted under D-01 with the residual wording in Section 11.3 | 2.6 decision; 2.9 audit | **OPEN** (race not eliminated; D-01 not accepted) |
| P0-05 | Binding revalidates root, ancestors, creation, operations, journal, lock, quarantine, parent, target | 2.6 | **OPEN** |
| P0-06 | Single writer enforced and lock identity stable; same/cross-process tests | 2.6 | **PARTIAL** (component behavior, no public admission test) |
| P0-07 | Create never overwrites; no-replace rename tested | 2.6 | **PARTIAL** (test exists; temp staging changes path) |
| P0-08 | Replace quarantine-before-overwrite and safe compensation | 2.6 implementation; 2.7 crash | **OPEN** |
| P0-09 | Delete excluded from the production surface **or** implemented with quarantine/purge | 2.5 exclusion decision | **EXCLUDED** (design-only; see Section 9.8.6) |
| P0-10 | Intent durable before every side effect; journal v2 append/flush before the content action | 2.6 implementation; 2.7 process crash | **OPEN** |
| P0-11 | **Process-crash** recovery diagnosis and limited apply; power-loss durability is a separate claim (T-39) and is not included in P0-11 | 2.7 | **OPEN** (not implemented) |
| P0-12 | All project-content writes migrated to the strict boundary, including ProjectStore | 2.8 | **OPEN** |
| P0-13 | Fail-closed error boundary; no raw exception escapes; status truthful | 2.6 | **OPEN** |
| P0-14 | Independent production audit after remediation | 2.9 | **NOT STARTED** |

#### 9.8.3 Phase exit criteria

| Phase | Exit criterion (all must hold) | What it must **not** claim |
|---|---|---|
| 2.5 | This design reviewed; every P0 gate has an owner, a test ID, or a documented exclusion; no code/test/protected file changed | Not an admission; no gate is PASS merely because it is designed |
| 2.6 | P0-01 approved; P0-02, P0-03, P0-04a, P0-05, P0-06, P0-07, P0-08 (non-crash), P0-10, P0-13 PASS; P0-04b either PASS (race eliminated) or PASS WITH ACCEPTED RESIDUAL (D-01 owner sign-off); D-11 limitation recorded | Must not claim P0-11, P0-12, or P0-14; must not say "all remaining P0 except P0-12/P0-14" while recovery is deferred |
| 2.7 | P0-11 PASS for **process crash**; crash/recovery portions of P0-08 and P0-10 PASS across Section 7.6; no silent loss or blind rollback; T-39 is either PASS on a disposable VM or BLOCKED with the limitation stated | Must not claim P0-12 or P0-14; must not claim replace is production-safe merely because recovery works; must not call T-39 BLOCKED a power-loss PASS |
| 2.8 | P0-12 PASS; project-content writes only through the strict boundary; L7 tests | Must not claim P0-14 or production admission |
| 2.9 | P0-14 PASS; independent audit re-runs all gates; P0-04b residual re-verified against its owner sign-off; T-39 limitation rechecked | Must not convert an accepted residual into "race fixed" |
| 3.0 | Every P0 gate is PASS or the single allowed `P0-04b` PASS WITH ACCEPTED RESIDUAL; no OPEN/PARTIAL/DEFERRED gate; T-39 is PASS **if** a power-loss claim is made, otherwise the admission text states the limitation | No production claim if any gate is merely designed, skipped, or environment-blocked; no "power-loss safe" wording without T-39 PASS |

Consequences:

- **2.6 cannot be the last remediation phase.** It closes the non-recovery implementation gates; P0-11 remains for 2.7, and P0-12/P0-14 remain for 2.8/2.9.
- **Recovery is not a 2.6 deliverable.** A 2.6 exit that depends on recovery behavior is a roadmap violation.
- **P0-04b is a decision, not a code pass.** If the owner accepts the residual, the gate status is `PASS WITH ACCEPTED RESIDUAL` and the production claim must repeat the residual. If the owner does not accept it, 2.6 cannot exit and the design needs a different hardlink defense.
- **P0-01 is not closed by writing this document.** It closes only with an explicit approval record naming the threat model revision and B2 boundary.
- **Power loss is a separate claim.** P0-10/P0-11 may PASS for process crash while T-39 is BLOCKED. That is not a contradiction: 3.0 may ship the process-crash-safe feature with the limitation, but it must not claim power-loss durability. If the owner requires power-loss safety, 3.0 is blocked until T-39 PASS.

#### 9.8.4 Gate-to-test map

A gate is not admitted by a prose claim or by "the suite is green". Each row names the tests that must fail when the gate's invariant is broken. A single test cannot be the sole evidence for two unrelated gates; where a test covers two gates, both rows name it and the assertions must cover both.

| Gate | Mandatory admission tests |
|---|---|
| P0-01 | Reviewer/owner sign-off record; no runtime test |
| P0-02 | T-46, T-47, T-48, T-49, T-50, T-51, T-52; plus the D-11 limitation sign-off |
| P0-03 | T-13, T-14, T-15, T-16, T-17; T-16 requires a symlink-capable environment and is `BLOCKED` until then |
| P0-04a | T-06, T-07, T-08; nlink check at each checkpoint, not only pre-open |
| P0-04b | T-08 race harness plus the D-01 decision; if accepted, the test documents the residual, it does not prove elimination |
| P0-05 | T-10, T-11, T-59; **T-12 is mandatory** (`_verify_binding` no-op must fail the suite) |
| P0-06 | T-23, T-24, T-25 |
| P0-07 | T-18 |
| P0-08 | T-19, T-20, T-21, T-22, T-53, T-60; Section 7.6 classifications and Section 7.7.2 actions |
| P0-09 | None (excluded); Section 9.8.6 is the verification, and any future delete reopens the gate |
| P0-10 | T-27, T-28, T-29, T-55; T-38 at each journal event |
| P0-11 | T-38, T-53, T-60; diagnose/apply ownership from Section 7.7.2; T-39 is **not** required for process-crash P0-11 |
| P0-12 | T-45 plus L7 end-to-end tests |
| P0-13 | T-30, T-31, T-32, T-44, T-61 |
| P0-14 | Independent audit report, not a test; re-runs T-01..T-61 as applicable |

`T-12` deserves emphasis: if making `_verify_binding` a no-op leaves the suite green, then the suite does not test binding, regardless of how many other tests pass. It is a gate-level mutation, not a nice-to-have.

#### 9.8.5 Prototype regression tests versus admission tests

| Property | Prototype regression | Admission |
|---|---|---|
| Purpose | Catch the specific 2.4 defects | Prove the invariant under adversarial conditions |
| Levels | L0- mostly | L2- |
| Fault injection | ad hoc monkeypatch | `FaultPlan` at named phases, private constructor |
| Handle check | diagnostic delta | Section 9.1.1 100-iteration procedure |
| Mutation | optional | required: disable the check, test must fail |
| Evidence | test log | JSONL with OS, revision, identities, journal, handle samples |
| Green suite | necessary | not sufficient |

The current 26 focused tests are a prototype regression suite. Promoting them to admission requires adding the missing L2- cases and the mandatory mutations; the count itself is not evidence.

#### 9.8.6 P0-09 exclusion semantics

`P0-09` is `EXCLUDED` for the 2.x production surface. The exclusion is a **scoped phase decision**, not a permanent architectural restriction and not a PASS.

- **No Delete/Purge surface.** The public `SafeWriter` API must not expose `delete`, `remove`, `purge`, `empty_quarantine`, or a destructive GC callable. The CLI must not expose a `delete`, `purge`, `clean-quarantine`, or equivalent subcommand/flag. ProjectStore must not call one.
- **No incidental delete.** Normal create/replace may delete only an owned temp or an owned empty parent directory under Section 7.2.5/Section 7.7.2; that is not a Delete feature. It must never remove a target, a quarantine, a user file, or an unknown object.
- **Verification.** API inventory (public methods and CLI parser), a negative import/call test, a CLI help snapshot, and a source search for destructive calls. The evidence is recorded under P0-09 as "excluded surface verified", not as "delete tested".
- **Reopening event.** Adding any callable Delete/Purge, any `--delete`/`--purge` flag, quarantine purge, destructive GC, or a caller that can invoke one reopens P0-09 as a new P0 gate. It requires its own design, quarantine/purge protocol, tests, and owner decision; it cannot inherit this exclusion.
- **Documentation.** README/status may say "delete is not part of the 2.x surface"; it must not say "delete is supported" or "delete passed".

`P0-09` is not marked PASS because it is excluded. It remains a documented exclusion until 3.0, where the admission text must repeat it.


### 9.9 Admission decision

**Production admission: BLOCKED.**

Reasons, in order:

1. **P0-04b:** the same-user hardlink race is not eliminated. D-01 is not accepted, so the gate is OPEN, not `PASS WITH ACCEPTED RESIDUAL`.
2. **P0-02:** the root trust record is specified but not implemented; no enrollment, ACL verification, or corruption/rollback behavior is executable.
3. **P0-05/P0-10:** binding revalidation and journal v2 are designs; the no-op mutation still passes the current suite.
4. **P0-08/P0-11:** replace compensation and recovery are designs; the crash matrix and ownership transitions are untested.
5. **P0-03:** symlink refusal is unverified on this machine; junction refusal alone is not enough.
6. **P0-12:** ProjectStore still writes by path.
7. **P0-14:** no independent production audit exists.
8. **P0-01:** the threat model in this revision has not been approved by the owner; this document is the review input, not the approval.

The current test suite is a prototype regression suite, not an admission suite. A green run does not close any gate in Section 9.8.2.

---

---

## 10. Phased roadmap

The sequence matters more than the calendar. Each phase has an exit criterion that is a test artifact, not a statement of intent. No phase starts until the previous phase's P0 gates pass, except for explicitly parallel, non-overlapping work.

### 10.1 Phase 2.5 - Admission design (this document)

Deliverables:

- This design.
- Evidence appendix with reproducible experiments.
- Admission checklist and verdict.

Exit criteria:

- An independent reviewer can trace every P0 requirement to a design section and a test ID.
- Every Windows assumption used by the protocol has either a documentation citation or a reproducible experiment.
- No code, test, ProjectStore, CLI, or protected file has been modified.

Status: **document complete upon delivery**; the design review closes only with the verdict and P0-01 approval. Admission remains blocked.

### 10.2 Phase 2.6 - Prototype remediation (no integration)

Scope:

- Remaining P0 fixes R-05..R-13 in `veyra_creation/safewriter/`; R-01..R-04 are closed and only need regression coverage.
- R-15..R-17 validation and test gaps; R-14 is closed and only needs regression coverage.
- New tests to the L2-L4 levels; fault hooks behind a private constructor.
- No ProjectStore/CLI integration; no user-facing flag changes.

Deliverables:

- Corrected anchors/writer/lock/journal.
- An admission-suite run on Python 3.14 and 3.15, Windows 11, NTFS.
- A test-evidence JSONL file.
- A remediation report that lists every gate this phase owns as PASS/PARTIAL/OPEN, and lists P0-11/P0-12/P0-14 as deferred rather than claiming them.

Exit criteria:

- P0-01 is approved (owner/security sign-off naming this revision), and P0-02, P0-03, P0-04a, P0-05, P0-06, P0-07, P0-08 (non-crash), P0-10, and P0-13 are PASS under Section 9.8.1. P0-03 requires T-16 on a symlink-capable environment; if T-16 is `BLOCKED`, 2.6 exit is `BLOCKED`, not PASS. D-11 is recorded as an accepted limitation or a separate design.
- P0-04b is either PASS (the race is eliminated) or `PASS WITH ACCEPTED RESIDUAL` with the D-01 sign-off recorded. If neither, 2.6 does not exit.
- P0-11, P0-12, and P0-14 are still OPEN/NOT STARTED and are explicitly not claimed by this phase. P0-09 remains `EXCLUDED`; 2.6 must not add a Delete/Purge surface. T-39 (power loss) is not a 2.6 claim.
- The mutation suite catches every disabled check, including `_verify_binding` (`T-12`).
- No handle growth over the Section 9.1.1 procedure: 100 iterations per known leak path, final quiescent count equal to baseline.
- No temp/quarantine orphan after any injected failure.
- A second engineer can reproduce the suite from a clean checkout using only the documented commands.

Explicitly out of scope: recovery implementation (P0-11), delete (P0-09), ProjectStore migration (P0-12), and independent audit (P0-14). Hardlink-race elimination is not assumed: P0-04b governs whether it is eliminated or owner-accepted.

### 10.3 Phase 2.7 - Crash and recovery harness

Scope:

- Recovery state machine against the v2 journal.
- Read-only `--diagnose`; explicit `--apply` with confirmation.
- Crash injection at every event (process kill and `TerminateProcess`).
- Power-loss runs on a disposable VM. If unavailable, T-39 is `BLOCKED` and the power-loss claim remains unverified; it is not converted to PASS.

Deliverables:

- Recovery classifications for all crash points.
- A matrix of `(last event, filesystem state) -> classification -> action`.
- Tests that no recovery action overwrites a target that is not proven to be the operation's own output.
- Documentation that distinguishes "journaled/recoverable" from "power-loss safe".

Exit criteria:

- P0-11 is PASS for **process crash**, and the crash/recovery portions of P0-08 and P0-10 are PASS across every row of Section 7.6. T-39 is either PASS on a disposable VM or `BLOCKED`; a BLOCKED T-39 does not prevent process-crash P0-11, but it forbids a power-loss claim.
- Every injected crash maps to exactly one documented classification; no event/state pair is unclassified.
- Recovery apply follows Section 7.7.2: one content side effect per invocation, state-based idempotence, no overwrite of an unknown target, no delete of an unidentifiable object.
- A crash at every Section 7.6 boundary is tested; a second run after each crash is either a no-op or the documented next step, never a duplicate rename or delete. T-60 covers the six compensation cases.
- Read-only diagnose never appends or mutates, even when the journal is writable.
- A target restored by another writer (`COMMITTED` or `TARGET_RESTORED_BY_OTHER`) is never rolled back.
- P0-12 and P0-14 remain OPEN; 2.7 does not claim replace is production-safe.

### 10.4 Phase 2.8 - ProjectStore and CLI integration

Scope:

- Migrate every project-content write in `ProjectStore` to the strict boundary (R-18).
- Strict/best-effort/read-only mode selection; default for existing commands remains compatible.
- Protected-path set wired from the caller.
- Round-trip integration tests; no behavior change to the JSON/Markdown contracts unless explicitly versioned.

Deliverables:

- A write inventory with each path classified and routed.
- Integration tests for concurrent writer, swapped `creation`, protected file, crash during a ProjectStore operation.
- CLI documentation for the mode and the recovery command.

Exit criteria:

- P0-12 is PASS: no `open("w")`, `write_json_atomic`, or append-by-path remains on a project-content path, or each retained compatibility path is documented, tested, and cannot touch a protected file.
- Existing Phase 1/2A/2B behavior and CLI flags remain compatible; output bytes for unchanged inputs are stable except for documented fields.
- `report.md` and real logs are never opened for write; the protected-path set is enforced at the API boundary, not only by CLI convention.
- Crash during a ProjectStore operation is either recovered by Section 7.7.2 or refused before a content side effect.
- Recovery CLI usage is documented; strict mode is the project-content write path. If a non-strict path remains, P0-12 is not PASS.
- P0-14 remains NOT STARTED.

### 10.5 Phase 2.9 - Independent production audit

Scope:

- An auditor who did not write the remediation.
- Re-run the full matrix, not just the new tests.
- Adversarial review of the threat model and residual risks.
- Confirm the protected-file and Git constraints were respected.

Exit criteria:

- P0-14 is PASS: an auditor who did not write the remediation re-runs the full matrix and signs the evidence, not just the new tests.
- Every P0 gate is independently verified as PASS, or `P0-04b` is verified as `PASS WITH ACCEPTED RESIDUAL` against the recorded owner sign-off. If the sign-off is absent, P0-04b is OPEN and 3.0 is blocked.
- Every residual risk is explicitly accepted by the project owner or eliminated; no residual is converted into "fixed" by wording.
- No test is admitted on the basis of "it passed once", a skipped test, or a design document.
- Protected-file and Git constraints were respected during remediation; `report.md` and real logs are unchanged by hash.

### 10.6 Phase 3.0 - Production admission

Only after 2.9:

- Mark the feature production-ready in README/status, with the exact supported scope.
- Publish the admission artifact (test evidence, residual risks, recovery runbook).
- State explicitly whether the claim is **process-crash recovery** or **power-loss durability**. The former needs P0-11; the latter additionally needs T-39 PASS on a disposable VM. If T-39 is `BLOCKED`, the admission text must say power-loss durability is unverified and must not use "power-loss safe".
- Keep delete out of the production surface unless separately designed and admitted; repeat the P0-09 exclusion.
- Do not convert `P0-04b` accepted residual, `P0-09` exclusion, or `T-39` BLOCKED into "fixed" or "passed".

### 10.7 Parallelism and dependencies

```
2.5 design ---> 2.6 remediation ---> 2.7 recovery ---> 2.8 integration ---> 2.9 audit ---> 3.0
                     |                  |
                     +-- test harness --+
```

- 2.6 and 2.7 can overlap only after the journal v2 format is frozen; otherwise recovery tests encode a moving format.
- 2.8 cannot start before 2.6's binding and temp staging are stable, because integration tests would otherwise assert the old behavior.
- 2.9 cannot be performed by the implementer; it is a separate role.

### 10.8 What is deliberately not in this roadmap

- A new database, service, queue, or daemon.
- A multi-agent workflow.
- Cross-platform support. Windows is the only supported production platform for this subsystem; POSIX behavior is out of scope.
- Network filesystems, cloud sync roots, and removable media as supported targets.
- Hardlink-proof storage. The residual race is accepted only if the owner signs off; otherwise the design needs a different storage primitive (out of scope).
- Delete and purge. They need their own threat model; treating delete as "replace with empty" would be wrong.

---

## 11. Risks, assumptions, and open decisions

### 11.1 Residual risks

| ID | Risk | Impact | Likelihood | Mitigation | Admission effect |
|---|---|---|---|---|---|
| X-01 | Same-user attacker creates a hardlink between the last nlink check and the commit rename | Target content may be replaced or a protected file modified through the alias | Low (requires a narrow race and same-user access) | nlink checks at every checkpoint; commit via no-replace rename; quarantine; refuse on observed nlink>1; accept residual only with owner sign-off | **P0-04a detects at checkpoints; P0-04b blocks unless owner-accepted** |
| X-02 | Same-user attacker replaces a directory/junction between check and use | Write redirected | Low-medium | no-delete directory handles; reparse refusal; binding revalidation; post-rename check | Detected, not eliminated; documented |
| X-03 | Power loss loses a directory entry despite a successful flush | Target or quarantine missing after reboot | Unknown | journal + recovery; VM testing; conservative wording | Durability claim **UNVERIFIED** until tested |
| X-04 | AV/EDR opens the target with delete/write share | Operation fails or is delayed | Medium in the real world | fail closed; retry only on explicit transient codes; never bypass sharing | Reliability risk, test needed |
| X-05 | ctypes struct layout differs on a future Windows/Python | Wrong flags/identity; dangerous behavior | Low | pin struct definitions; version tests; fail closed on size mismatch | P1 |
| X-06 | File ID reuse after deletion | Old identity accepted as current | Very low on NTFS but not impossible | compare volume serial + file ID + path + creation time where available; short session lifetime | Documented residual |
| X-07 | Root trust record is compromised or copied | Attacker controls the trusted identity | Low | Owner-only ACL; schema v2 digests; explicit enrollment; re-enrollment requires confirmation; no TOFU; no claim against B2 tamper | P0-02/P0-04b boundary |
| X-08 | Internal state (`creation/`) is visible/modifiable by other local users | Denial of service or state confusion | Medium on shared machines | project ACL guidance; binding; refuse on mismatch; do not claim multi-user isolation | Documented assumption |
| X-09 | Journal contains paths or hashes that leak sensitive information | Privacy | Low-medium | relative paths; no file contents; root hash only; retention policy | P1 |
| X-10 | Caller passes a protected path; API-level set missing | `report.md` or a user file modified | Medium until integrated | protected set; tests; CLI allowlist | P0 for integration |
| X-11 | ProjectStore continues path-based writes | All SafeWriter guarantees bypassed | High until 2.8 | write inventory; integration gate; no production claim before | P0-12 |
| X-12 | Recovery deletes an unidentifiable file | Data loss | Medium if implemented carelessly | recovery is read-only by default; no delete; quarantine; operator confirmation | P0-11 |
| X-13 | Resource exhaustion (handles, temp files, journal) | Denial of service | Medium | limits; refuse before side effect; leak tests; observability | P1 |
| X-14 | Test environment lacks symlink privilege | A false sense of coverage | High on default Windows | mark UNVERIFIED; require a capable environment for P0-03; do not infer from junction alone | P0-03 |
| X-15 | A "passing" suite is used as evidence without mutation checks | Regressions unnoticed | High (already happened) | mutation tests; evidence JSON; independent audit | P0-14 |

### 11.2 Security assumptions

1. **Attacker classes are not one class.** B1 is a non-malicious, non-cooperating writer (another tool, an editor, an antivirus, a sync client). B1 is in scope as a failure source: identity checks, no-replace commits, and fail-closed behavior must prevent Veyra from following a changed object. B2 is a malicious process running as the same user. Veyra has **no security boundary** against B2; it can detect some changes and fail closed, but B2 can edit the project, the journal, and the trust record directly. B3 is a different low-privilege user; Veyra is in scope only where the directory ACL denies B3 write access. B4 (administrator, kernel, backup/restore privilege, physical attacker) is out of scope.
2. The project root is on a local NTFS volume. ReFS, FAT/exFAT, network shares, cloud-sync placeholders, and removable media are unsupported.
3. The process account is trusted to run Veyra. If it is compromised, the attacker can call the APIs directly; no user-space design can stop that. This is the B2 boundary, stated once here and not re-litigated per feature.
4. The Windows API contract is as documented and as reproduced in Section 6; undocumented behavior is not relied on for safety.
5. The caller supplies a trusted root identity from the trust store or an equivalent trusted source. The library does not infer trust from a path. The trust record itself is B2-editable and is a consistency/anti-accident control, not an attestation.
6. Other writers are expected to cooperate with the executor lock. A non-cooperating writer is a residual risk (X-01/X-02), not an eliminated one. The lock excludes cooperating Veyra processes, not arbitrary writers.
7. `report.md`, `real_crash.log`, and user-declared files are protected by policy; the filesystem does not enforce this, so the API must.
8. Power-loss safety is not assumed. A successful `FlushFileBuffers` is necessary but not sufficient evidence for directory-entry persistence.
9. An nlink observation proves only the count at the checkpoint. It does not prevent a hardlink from being created after the checkpoint and before the rename. The race is a B2 residual; see D-01.
10. The trust store ACL (owner + `SYSTEM` + `Administrators`, no other allow ACE) is the B3 boundary. It does not stop B2. Copying the record to another user is detected by owner SID and ACL; same-user copying is not a security event.
11. First enrollment is a user confirmation of what the path resolves to at that moment. It is not cryptographic attestation of the object's history; a path already swapped before enrollment captures the attacker's object. Veyra never enrolls implicitly.

### 11.3 Open decisions

| ID | Decision | Recommended default | Why | Owner |
|---|---|---|---|---|
| D-01 | Accept the same-user hardlink race or change the storage primitive? | **Do not claim elimination.** Either (a) owner accepts it as `PASS WITH ACCEPTED RESIDUAL`, with the B2 boundary and user-facing warning, or (b) redesign the storage primitive. Silence or "the tests pass" is not acceptance. | No user-space race fix exists for an uncooperative same-user writer creating a hardlink between the last check and the rename. nlink checks detect at checkpoints; they do not close the window. | Project owner + security |
| D-02 | Trust store location | `%LOCALAPPDATA%\Veyra\trusted-roots.json`, per-user, owner-only ACL | Avoids a project-local file that an attacker can replace; survives project moves | Project owner |
| D-03 | Root enrollment UX | Explicit `trust-root --inspect/--enroll`; no first-write enrollment | TOFU would trust an attacker-planted root | Product + security |
| D-04 | Recovery default | `--diagnose` read-only; `--apply` explicit, per-operation confirmation | Prevents automated deletion of user data | Product |
| D-05 | Journal retention and archive | Two tiers: an active journal for the current operation/creation history, and a verified archive when the active file reaches `MAX_JOURNAL_BYTES` or `MAX_RECORDS`. Do not delete the last complete record, the quarantine identity evidence, or the archive. Cap by refusing writes (`E_JOURNAL_FULL`) until an explicit archive handoff verifies the archive copy and records `JOURNAL_ARCHIVED`. Archive retention is project lifetime unless the owner chooses a longer/shorter documented policy. | The journal is the audit and recovery trail; auto-deleting it destroys recoverability and hides an interrupted operation. A cap without a verified archive is data loss, not rotation. | Project owner |
| D-06 | Temp location | `creation/operations/tmp` | Keeps user business directories clean; one cleanup boundary | Engineering |
| D-07 | Protected set source | Caller-provided, with defaults for `report.md`/`real_crash.log` | Policy belongs to the caller; the library cannot know user intent | Engineering |
| D-08 | Delete/Purge support | Out of scope for 2.x, and no callable Delete/Purge surface is exposed (`P0-09`). Any callable delete, purge, quarantine GC, or `--delete`/`--purge` surface reopens P0-09 as a new gate and needs its own design. | Delete has different recovery and privacy semantics; an undocumented helper would bypass the quarantine-before-overwrite model. | Product |
| D-09 | Non-NTFS behavior | Refuse with `E_UNSUPPORTED_VOLUME` | Silent fallback would weaken every guarantee | Engineering |
| D-10 | Symlink support | Refuse; require a capable test environment for proof | Following links is an unacceptable redirect risk | Security |
| D-11 | Trust-record rollback and same-user tamper detection | **Do not claim detection.** Accept the documented limitation, or introduce an external monotonic/attested store as a separate design. Do not add a home-grown counter that B2 can rewrite. | A valid older record, or a record with a recomputed digest, is indistinguishable under the current schema. The ACL protects B3, not B2. | Project owner + security |

**Decisions needed before 2.6 starts:** D-01 and D-11. If D-01 is not accepted, P0-04b is OPEN, 2.6 cannot exit, and the design must be revisited before implementation. If D-11 is not accepted as a limitation, the trust store needs an external monotonic/attested design before P0-02 can be PASS.

### 11.4 What this design does not guarantee

- It does not make the project tamper-proof against a same-user attacker (B2), including one who wins the hardlink race or edits the journal/trust record directly.
- It does not detect a valid older trust record restored by the same user, or a same-user tamper that recomputes the digests.
- It does not attest the first enrollment; the user confirms the object at that moment.
- It does not guarantee power-loss durability; that requires VM evidence and even then is hardware/filesystem dependent.
- It does not protect against a compromised process, a malicious kernel driver, or an attacker with backup/restore privileges.
- It does not make `report.md` safe by itself; the caller must pass the protected set.
- It does not provide multi-user isolation or a security boundary between Windows accounts beyond the directory ACL.
- It does not make ProjectStore safe until every write is migrated.
- It does not define delete/purge semantics.
- It does not promise that a crash leaves the target in a "clean" state; it promises that the state is classified and recoverable without silent success.

---

## 12. Appendices

### 12.1 Appendix A - Source map

| Concern | File | Relevant symbols |
|---|---|---|
| Public writer API | `veyra_creation/safewriter/writer.py` | `SafeWriter`, `SafeWriter.create`, `.replace`, `._open_session`, `._verify_binding`, `._ensure_create_parent`, `._create_temp`, `._cleanup_temp` |
| Anchors/identity | `veyra_creation/safewriter/anchors.py` | `RootAnchor.open`, `.open_path`, `.verify`, `DirectoryAnchor`, `TargetAnchor`, `_compare_root` |
| Native bindings | `veyra_creation/safewriter/native.py` | `nt_create_file`, `nt_open_relative`, `nt_set_information`, `nt_query_information`, `nt_flush`, `_us` |
| Policy | `veyra_creation/safewriter/policy.py` | `validate_root_path`, `validate_simple_name`, `validate_relpath`, `VolumeFacts` |
| Locking | `veyra_creation/safewriter/lock.py` | `LockManager.acquire`, `LockHandle.release`, owner metadata |
| Journal | `veyra_creation/safewriter/journal.py` | `OperationJournal.append`, `.recover`, `.close`, `canonical_json` |
| Prototype tests | `tests/test_creation_safewriter.py` | 26 unique tests; duplicate and false positive fixed in 2.4R; binding mutation still passes |
| 2.4 report | `docs/phase-3.2.2.1-2.4-safewriter-prototype-report.md` | historical; lines 204/327 need correction |
| 2.4 audit | `docs/phase-3.2.2.1-2.4R-independent-reaudit.md` | P0/P1/P2 findings |
| 2.5 design | this file | admission artifact |

### 12.2 Appendix B - Experiment log

Environment: Windows 11 10.0.26300 x64, NTFS on `C:`, Python 3.15.0 (CPython), standard user, no Developer Mode. Experiments ran under `%TEMP%`, outside the repository. The repository was not used as an experiment root.

| ID | Question | Method | Observation | Conclusion |
|---|---|---|---|---|
| B-01 | Can a directory handle be opened with write access and flushed? | `CreateFileW` with `GENERIC_READ\|GENERIC_WRITE`, `FILE_FLAG_BACKUP_SEMANTICS`; `FlushFileBuffers` | Open succeeds; flush returns success | Possible; durability of the directory entry is not documented as guaranteed |
| B-02 | Does `FILE_OPEN_REPARSE_POINT` open a junction itself? | `mklink /J`, then `nt_open_relative` with the flag | attrs `0x1040`, tag `0xA0000003`, final path is the junction | Yes; do not follow; reject on tag |
| B-03 | Can a symlink be created for testing? | `mklink /D` | `You do not have sufficient privilege to perform this operation.` | Symlink refusal is **UNVERIFIED** on this machine |
| B-04 | Does a no-delete parent block child rename? | Parent share `READ\|WRITE`; child opened with `DELETE`; rename child | Child file rename succeeds; child directory rename succeeds; parent rename fails `WinError 32` | No-delete parent protects the parent name, not child commits |
| B-05 | Does `LockFileEx` block a second handle/process? | Two handles, same and cross process, `LockFileEx` | Second lock fails `WinError 33`; lock file rename/delete fails `WinError 32` | Cooperative lock works; file remains while held |
| B-06 | Does share mode prevent hardlink creation? | `CreateHardLinkW(new, existing, NULL)` with share READ and share 0 | Succeeds in both; nlink 1 -> 2; read-only attribute does not block | Share mode is not a defense against a **B2 same-user malicious** hardlink creator. It does not justify refusing B3 (a non-malicious writer denied write by ACL); B3 is handled fail-closed, not by a B2 claim. nlink checks + owner race decision are required. |
| B-07 | Does cross-directory rename work? | staging -> target parent, `FILE_RENAME_INFORMATION_EX` | Succeeds; collision with flags=0 returns `STATUS_OBJECT_NAME_COLLISION`; replace=true overwrites | Staging design is viable; no-replace is enforceable |
| B-08 | Does a live descendant block root rename? | Open root only vs open a file under it; rename root | Root-only: rename succeeds; live descendant: `WinError 5` | The 2.4 report's attribution to directory handles is wrong |
| B-09 | Can a file with an open handle be renamed/deleted? | Open target with desired access/share, then rename/delete | Rename with `DELETE` succeeds; delete via `FILE_DISPOSITION_INFO` succeeds when no other handle blocks | Commit can rename an open target; sharing still matters |
| B-10 | Does a lone surrogate escape name validation? | `validate_simple_name("\ud800")`, then create | `UnicodeEncodeError` at `native._us`/journal encoding | Validation must reject before native calls |

Notes:

- B-10 records a pre-2.4R observation. Current `policy._has_surrogate` and the journal encoder reject the surrogate before native I/O; the experiment is retained as rationale for R-14, not as a current defect.
- B-06 is a **B2** experiment: an uncooperative same-user process deliberately creates a hardlink. It must not be cited as evidence that **B3** (non-malicious interference) requires the same residual acceptance. B3 is in scope only as an accidental/failure source and is handled by fail-closed errors.
- The initial hardlink experiment used the wrong `CreateHardLinkW` argument order and produced `ERROR_FILE_NOT_FOUND`; it was discarded. The corrected run used `os.link` and the documented parameter order.
- The 2.4 report's `:204` claim ("root rename blocked by directory handle sharing") is contradicted by B-08; the true blocker was a live descendant handle.
- No experiment wrote to `report.md`, `real_crash.log`, or any repository file.

### 12.3 Appendix C - Error/status glossary

| Code | Meaning | Retryable | Side effect possible? |
|---|---|---|---|
| `SUCCEEDED` | Operation completed and verified; all postconditions and the terminal journal record are durable | No | Intended, verified |
| `REFUSED` | Precondition failed before any content side effect | Only after the precondition is fixed, with a new operation/batch | No |
| `FAILED` | Operation failed; state is known and the target is unchanged | New operation only; do not reuse the batch | No (temp may have existed and been cleaned) |
| `COMMIT_UNCERTAIN` | The content postcondition is uncertain; the target may be new | No automatic retry | Yes, uncertain |
| `RECOVERY_REQUIRED` | State is uncertain or a side effect occurred without a terminal record; inspect/recover | No automatic retry | Yes |
| `OK` | **Not a public status.** A legacy convenience value; new API must not emit it | n/a | n/a |
| `E_INVALID_PATH` | Relative path is malformed or not a simple name | No | No |
| `E_ROOT_UNTRUSTED` | No trusted identity/enrollment for the root | After explicit enrollment | No |
| `E_ROOT_CHANGED` / `E_ROOT_MISSING` / `E_ROOT_MOVED` | Root identity/path/volume changed or is gone | No; explicit re-enrollment where allowed | No |
| `E_VOLUME_CHANGED` | Volume serial/GUID or filesystem changed | No | No |
| `E_TRUST_SCHEMA_UNSUPPORTED` | Trust record `schema_version` is not exactly 2 | No; explicit migration | No |
| `E_TRUST_RECORD_CORRUPT` | Trust record JSON/schema/digest/duplicate invalid | No; explicit replace | No |
| `E_TRUST_RECORD_MISSING` | Trust record absent after interrupted enrollment | Restore backup or re-enroll | No |
| `E_TRUST_RECORD_CHANGED` | Trust file identity changed during load | No; retry load | No |
| `E_TRUST_RECORD_FOREIGN` | Trust record owner SID is not the current user | No | No |
| `E_TRUST_ACL_UNSAFE` / `E_TRUST_ACL_CHANGED` | Trust store ACL is not owner-only or drifted | No; explicit re-enrollment | No |
| `E_TRUST_CONFLICT` | Entry exists with a different identity/path and no `--replace-trust` | No; explicit replace | No |
| `E_BINDING_CHANGED` | An internal component changed identity | No; diagnose | No after detection |
| `E_REPARSE_REFUSED` | Reparse point encountered at a component | No | No |
| `E_HARDLINK_REFUSED` | `nlink > 1` observed on a protected object | No | No before; uncertain after |
| `E_TARGET_EXISTS` | No-replace target already exists | No | No |
| `E_TARGET_MISSING` | Expected target absent | No | No |
| `E_TARGET_CHANGED` | Target identity/hash/nlink differs from the expected role | No; diagnose | No before; uncertain after |
| `E_TARGET_UNSUPPORTED` | Target type/volume unsupported | No | No |
| `E_PROTECTED_PATH` | Target is protected by caller policy | No | No |
| `E_LOCK_BUSY` | Another writer holds the executor lock | Yes, later | No |
| `E_LOCK_CHANGED` / `E_LOCK_METADATA` | Lock identity changed or metadata could not be persisted | No | No |
| `E_JOURNAL_CORRUPT` | Journal has a complete invalid record | No; diagnose | No before; uncertain after |
| `E_JOURNAL_FULL` | Journal size/record limit reached | After verified archive | No before; uncertain after |
| `E_JOURNAL_RECORD_TOO_LARGE` | A canonical record exceeds `MAX_RECORD_BYTES` | No; fix the event | No |
| `E_JOURNAL_APPEND` | Append/flush failed | No automatic; see Section 7.8.3 | Depends on phase |
| `E_JOURNAL_TAIL_UNREPAIRED` | Read-only diagnosis found a torn tail that needs explicit repair | Explicit apply | No |
| `E_JOURNAL_VERIFY` | Reopen/verify of an appended record failed | No; diagnose | Uncertain |
| `E_TEMP_CREATE` / `E_TEMP_WRITE` / `E_TEMP_VERIFY` | Temp staging failed | New operation | Temp only |
| `E_COMMIT_CONFLICT` | Rename collision or target appeared during commit | No; diagnose | Target unchanged or `new`; uncertain if `?` |
| `E_COMMIT_UNCERTAIN` | Commit outcome could not be verified | No automatic | Yes |
| `E_ROLLBACK_FAILED` | Compensation rename failed; state preserved | No; diagnose | Yes, uncertain |
| `E_FLUSH_CONTENT` | Content flush failed before publication | New operation | No if before; uncertain after |
| `E_FLUSH_DIRECTORY` | Directory-entry flush failed/unverified after publication | No automatic | Content published; durability unverified |
| `E_FLUSH_JOURNAL_TERMINAL` | Terminal journal flush failed | No automatic | Content may be correct; no SUCCEEDED |
| `E_DURABILITY_UNCERTAIN` | Legacy umbrella; **not emitted by new code** | Treat as `RECOVERY_REQUIRED` | Depends |
| `E_RECOVERY_REFUSED` | Recovery classification does not permit apply | No | No |
| `E_RECOVERY_NOT_ALLOWED` | Apply requested without authorization/precondition | No | No |
| `E_ORPHAN_TEMP_DELETE_FAILED` | Owned temp could not be deleted | No; diagnose | Temp only |
| `E_UNICODE` | Invalid UTF-16/UTF-8 name or path | No | No |
| `E_UNSUPPORTED_VOLUME` | Not local NTFS | No | No |
| `E_LIMIT` | Path/journal/resource limit | No | No |
| `E_IO` | Native I/O failure mapped to a structured code | Maybe after observation | Depends on phase |
| `E_ARCHIVE_*` | Archive/handoff failures (future) | No | Depends |


### 12.4 Appendix D - References

- Microsoft, `CreateFileW`: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
- Microsoft, `SetFileInformationByHandle`: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle
- Microsoft, `FILE_RENAME_INFO`: https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_rename_info
- Microsoft, `LockFileEx`: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex
- Microsoft, `FlushFileBuffers`: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers
- Microsoft, `CreateHardLinkW`: https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createhardlinkw
- Microsoft, `GetFileInformationByHandle`: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandle
- Microsoft, `FILE_ID_INFO`: https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-file_id_info
- Microsoft, `FSCTL_GET_REPARSE_POINT`: https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-fsctl_get_reparse_point
- Microsoft, `NtCreateFile`: https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile
- Microsoft, Reparse points: https://learn.microsoft.com/en-us/windows/win32/fileio/reparse-points
- Microsoft, Hard links and junctions: https://learn.microsoft.com/en-us/windows/win32/fileio/hard-links-and-junctions
- Microsoft, File systems: https://learn.microsoft.com/en-us/windows/win32/fileio/file-systems
- Microsoft, `GetNamedSecurityInfoW`: https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getnamedsecurityinfow
- Microsoft, `GetSecurityDescriptorSddlForm`: https://learn.microsoft.com/en-us/windows/win32/api/sddl/nf-sddl-getsecuritydescriptorsddlform
- Microsoft, `GetVolumeNameForVolumeMountPointW`: https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumenameforvolumemountpointw
- Microsoft, `SHGetKnownFolderPath`: https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shgetknownfolderpath

### 12.5 Appendix E - Delivery self-check

| Check | Result |
|---|---|
| Only the R.1 design document and the R.1 change report were written | Yes |
| Original 2.5R design document opened for write | No; its SHA256 is unchanged |
| Existing source/tests changed | No |
| `report.md` opened for write | No |
| `real_crash.log` opened for write | No |
| Git add/commit/push/reset/checkout/clean/rebase | No |
| New experiments in the repository | No; B-01..B-10 are previous external experiments |
| Third-party dependency added | No |
| Production code changed | No |
| ProjectStore/CLI integrated | No |
| Delete/Purge implemented | No |
| Recovery implemented | No |
| Language | English (new/revised documents); existing Chinese docs untouched |
| R.1 change report | `docs/phase-3.2.2.1-2.5R.1-design-consistency-change-report.md` (created separately) |

Protected baseline hashes:

- `report.md`: `A11D09D49217CA3C6CD7C269268C148558EA488067E8D1399BCAFD7ABBC2D68D`
- `real_crash.log`: `CC1418B7690284787B55D3417383F23DD6DEAA47619BC41702F9F33895DCD54D`
- Repository revision: `88c5107707cc2b58cead814c14b005bb3af7a11b` (`main`)
- Original 2.5R design SHA256: `a99ddc38d00b2ece0a6b69c38a1e56dfa4de0c4653f4380419827cf23f9c8228`

These must be rechecked at delivery; if a protected hash changed, stop and report it rather than continuing. The original 2.5R is retained unmodified for audit; R.1 supersedes it for review.

### 12.6 Appendix F - Reviewer checklist

A reviewer should be able to answer "yes" to all of these before recommending 2.6:

1. Is the threat model split into B1/B2/B3/B4, and does every claim state which class it addresses?
2. Is the trust root explicit, out of the project tree, owner-only, schema-versioned, and free of implicit first-use enrollment?
3. Does trust-record loading cover move, volume change, recreation, corruption, copy, rollback, and ACL drift without auto-adoption?
4. Is every directory handle opened without delete sharing, and is the root chain held to the volume root?
5. Is every reparse point refused at open, and is the symlink case either tested on a capable machine or marked `BLOCKED` rather than PASS?
6. Is every protected object checked for `nlink == 1` before and after each side effect, and is the residual race explicitly accepted or eliminated? Is B-06 cited as B2, not as a B3 justification?
7. Is every internal component revalidated against the held handle immediately before commit?
8. Is the journal intent durable before each side effect; is a torn tail distinguished from a complete invalid record; is archive handoff defined?
9. Does the create/replace transition table cover failure before/after every side effect, quarantine mismatch, final-rename failure, process termination, and all compensation branches?
10. Does the recovery table define one classification per observable state, ownership, one side effect per invocation, state-based idempotence, and no-delete-unknown? Are recovery apply actions explicitly authorized?
11. Is a process crash at each transition classified, and is recovery read-only by default? Is power loss separated and marked `BLOCKED` without a VM?
12. Are all project-content writes routed through the boundary, including ProjectStore?
13. Is the test suite mutation-resistant, does `_verify_binding` no-op fail, and does the handle test use Section 9.1.1 rather than a warm-up phrase?
14. Does every P0 gate have a phase owner, a test ID or exclusion, and a status that is not "designed = passed"?
15. Are unsupported volumes, delete, and the B2 same-user attacker explicitly out of scope? Does the document avoid claiming power-loss safety without VM evidence?
16. Does P0-09 have exact exclusion semantics (no callable Delete/Purge surface, reopening event, verification) rather than a vague "out of scope"?
17. Does the evidence schema require machine-verifiable fields, including the full handle sample set, journal identities, and artifact hashes?
18. Do R-19..R-27 each have an owner, target phase, evidence, and closure condition, and are they clearly not claimed as closed by this design?

### 12.7 Appendix G - Final verdict

**READY FOR INDEPENDENT DESIGN REVIEW.**

This is the R.1 re-review verdict. The 2.5R.1 revision resolves the internal contradictions that made the earlier document un-reviewable:

- the P0 gates are split by phase owner and do not claim that 2.6 closes recovery or integration;
- B1/B2/B3/B4 are separated, and no claim implies a same-user security boundary;
- the trust record has a versioned schema, an ACL verification path, explicit enrollment, and defined move/corruption/copy/rollback/tamper behavior;
- create and replace have transition tables with failure points, compensation branches, ownership rules, and process-crash classifications;
- journal retention has an active/archive split rather than a size cap that silently deletes the audit trail;
- the handle criterion is a mechanical 100-iteration procedure, not a warm-up phrase;
- every P0 gate maps to tests, an exclusion, or an independent audit;
- the test evidence is a machine-verifiable JSONL contract, not prose.

This is **not** an admission verdict. Production remains **BLOCKED**, and no P0 gate is PASS merely because this document specifies it.

Before 2.6 starts, the project owner must record decisions on:

- **D-01** - Accept the same-user hardlink race as `PASS WITH ACCEPTED RESIDUAL`, or commission a different storage primitive. The race is not eliminated; nlink checks only detect it at checkpoints.
- **D-11** - Accept that a valid older trust record or a same-user recomputed digest is not detectable, or commission an external monotonic/attested store.

If D-01 is not accepted, P0-04b stays OPEN and 2.6 cannot exit. If D-11 is not accepted as a limitation, P0-02 cannot be PASS. **D-05** is a retention/archive decision, not an admission gate; the current design defines the safe behavior, and an owner policy may lengthen retention but may not delete the last complete record or the archive.

The next action is an independent re-review of this document. No implementation starts until the review is complete and the owner decisions are recorded. This revision does not modify code, tests, ProjectStore, the CLI, `report.md`, or `real_crash.log`, and it does not modify the original 2.5R file.

### 12.8 Appendix H - R.1 change summary

R.1 is a targeted remediation of the independent review's R1-2 findings. It is documentation-only. The original 2.5R remains the audit baseline; R.1 is the review candidate.

| Correction | Where addressed | Evidence |
|---|---|---|
| R1 Public status/`OK` contradiction | Section 1.6, Section 4.8, Section 4.8.1, Section 7.8.1, Section 12.3 | Five statuses only; `OK` explicitly not public; state-dependent mapping |
| R2 Journal append/compensation contradiction | Section 7.3.1, Section 7.8.3 | C1- table; `rollback_attempted=false` unless `ROLLBACK_INTENT` is durable |
| R3 RPO inconsistency | Section 6.7.1, Section 7.2.3, Section 7.3.1 | DUR-1..DUR-4; commit intent before rename; terminal append after postcondition |
| R4 Crash matrix and recovery split | Section 7.2.1, Section 7.2.2, Section 7.6, Section 7.7 | One classification per row; explicit apply only; one content side effect |
| R5 P0 gate/exit/test consistency | Section 9.8, Section 10.2-10.6 | P0-03/T-16 `BLOCKED`; P0-09 exclusion; P0-11 process-crash scope; 3.0 power-loss limitation |
| R6 P0-09 exclusion semantics | Section 9.8.2, Section 9.8.6, Section 11.3 D-08 | No callable Delete/Purge; reopening event; verification |
| R7 Journal retention contradiction | Section 7.5.6, Section 11.3 D-05 | Active/archive tiers; no auto-delete of last complete record or quarantine evidence |
| R8 B3/B2 category error | Section 12.2 B-06 and note | B-06 is B2; B3 is fail-closed, not residual acceptance |
| R9 P0-10/P0-11/T-38/T-39/2.7/3.0 contradiction | Section 9.6, Section 9.8.3, Section 10.3, Section 10.6 | Process crash vs power loss; T-39 `BLOCKED`; no power-loss claim without VM |
| R10 Missing P1 ownership | Section 8.5 | R-19..R-27 owner, phase, evidence, closure |
| R11 Evidence schema self-contradiction | Section 9.7, Section 9.7.1 | JSONL schema; full handle samples; artifact hashes; no bare `handle_delta:0` |
| R12 Closure matrix | Section 1.6, this appendix, separate change report | R1-2 mapping to sections and checks |

No code or test was changed. The implementation status in Section 9.3/Section 9.4 is the 2.4R status, not a post-2.5R.1 claim. The design remains a specification; production admission is `BLOCKED`.

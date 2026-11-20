# Contributing to Veyra

Thanks for helping improve Veyra. This document covers local setup, testing, and pull request expectations.

## Development setup

- Python 3.10 or later is recommended (development and testing environment is 3.10+). Standard library only; no third-party packages required.
- No API key is needed for local development and testing.
- The CLI replaces `report.md` in the current working directory (the write is atomic, but the previous report is lost), so run it from a scratch directory, pass `-o <path>`, or move any report you want to keep.

## Local testing (no API key, no network)

```shell
# Run the tool in a unique scratch directory to avoid overwriting report.md
# (Use absolute paths for the script and log, output goes to the new current dir)

# PowerShell:
$RepoRoot = (git rev-parse --show-toplevel).Trim()
$TempDir = Join-Path $env:TEMP "veyra-test-$(New-Guid)"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
cd $TempDir
$env:TRIAGE_LOCAL_MODE="1"
python "$RepoRoot\triage_agent.py" "$RepoRoot\demo\sample-crash.log"

# Bash:
REPO_ROOT=$(git rev-parse --show-toplevel)
TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"
TRIAGE_LOCAL_MODE=1 python "$REPO_ROOT/triage_agent.py" "$REPO_ROOT/demo/sample-crash.log"
```

`demo/sample-crash.log` is synthetic data for testing and demonstration only. Review the generated `report.md` and confirm that:

- the eight report fields are present;
- the evidence section lists the expected `E1`, `E2`, ... IDs with real line ranges;
- the embedded prompt-injection line is recorded as `suspicious_text` (it must never be executed);
- no exception or unexpected traceback appears on the terminal.

`crash_log.txt` in the repository root is another synthetic log you can use.

## Automated tests

The regression suite uses only the Python standard library (`unittest`) and needs no API key:

```shell
python -m unittest discover -s tests
```

Test modules live in `tests/` (`test_environment.py`, `test_evidence.py`, `test_file_safety.py`, `test_cli.py`, `test_json_output.py`, `test_skill_validation.py`, `test_synthetic_fixture.py`). Every test runs in a `tempfile.TemporaryDirectory()`; none of them write to the repository root. Keep the suite green when changing extraction, validation, report writing, the JSON contract, or the Skill files.

If you change the JSON output, update `docs/json-contract.md`, `docs/report.schema.json`, and `tests/test_json_output.py` together. The contract's `schema_version` is independent of the tool version; bump it only for incompatible changes and explain the migration in the PR.

## Test data

Never commit a real player crash log. Real logs stay local and are covered by exact `.gitignore` rules (`real_crash.log`, `*.real-crash.log`). Tests use fully fabricated logs under `tests/fixtures/`, marked `SYNTHETIC TEST FIXTURE` in the header; keep them free of real nicknames, UUIDs, IPs, absolute paths, hardware details, and worlds. If extraction behavior changes, update the fixture and its expected line numbers in `tests/test_synthetic_fixture.py` together.

## Skill changes

The Skill in `skills/mod-issue-triage/` is a prompt-level workflow, not a program. When you change it:

- keep the evidence rules intact: log facts may only cite evidence IDs that already exist in the supplied JSON, with the line ranges copied from it; player statements and AI hypotheses must stay labeled as such;
- keep the seven section headings and the three reply scenarios, or update `references/report-template.md`, `SKILL.md`, and the README sections together;
- update `skills/mod-issue-triage/scripts/validate_issue_json.py` if the contract checks need to change, and make sure it stays consistent with `docs/report.schema.json` (a test compares them);
- keep eval cases in `skills/mod-issue-triage/evals/cases/` synthetic and free of personal data; each case's `report` must pass the validator;
- do not describe human review of Skill output as automated testing. The repository has no LLM-based Skill eval runner; the eval README must keep saying which checks are automated and which are manual.

## Testing installer changes

The installer scripts live in `scripts/` (`install.ps1` and `install.sh`). Test them only against temporary directories, never against a real DSH skill directory:

- install into a new empty directory and confirm `mod-issue-triage/.veyra-install.json` exists;
- run update (`-Update` / `--update`) and confirm no `.veyra-tmp-*` or `.veyra-bak-*` directory remains;
- run uninstall (`-Uninstall` / `--uninstall`) and confirm the skill directory is gone while sibling files remain;
- verify rejection of relative destinations, paths containing `..`, a stale `.veyra-operation.lock`, invalid marker JSON, and symlinked destinations;
- fault-injection tests (copy failure, swap failure, cleanup failure) must run against a temporary copy of the script; never weaken the safety checks in the checked-in script to make a test pass.

## Pull request guidelines

- Keep changes focused: one topic per pull request.
- Do not edit `skills/mod-issue-triage/` or `triage_agent.py` without a clear reason; describe the behavior change in the PR.
- Do not commit real crash logs, player names, server addresses, UUIDs, API keys, or other personal data. Test data must be synthetic.
- Keep documentation in sync: if you change commands, environment variables, or output behavior, update `README.md` and `README_zh.md`.
- Installer scripts (`scripts/`), the CI workflow (`.github/workflows/test.yml`), and the demo preview report (`demo/expected-report-preview.md`) already exist. Change them only when your PR is explicitly about that area, and keep the safety checks in the installers intact.
- Explain how you tested the change in the PR description. Local-mode output is sufficient for most changes.
- Do not run API calls with someone else's key, and never paste keys into issues or pull requests.

## Reporting bugs

Open an issue using the bug report template in `.github/ISSUE_TEMPLATE/bug_report.md`. Redact personal information from any log you attach.

## License

By contributing, you agree that your contributions are licensed under the MIT License (see `LICENSE`).

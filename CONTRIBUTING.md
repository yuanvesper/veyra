# Contributing to Veyra

Thanks for helping improve Veyra. This document covers local setup, testing, and pull request expectations.

## Development setup

- Python 3.10 or later is recommended (development and testing environment is 3.10+). Standard library only; no third-party packages required.
- No API key is needed for local development and testing.
- The CLI writes `report.md` into the current working directory and silently overwrites an existing file, so run it from a scratch directory or move any report you want to keep.

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
- Do not add installers, CI workflows, or generated preview reports unless the change is explicitly scoped for that stage.
- Explain how you tested the change in the PR description. Local-mode output is sufficient for most changes.
- Do not run API calls with someone else's key, and never paste keys into issues or pull requests.

## Reporting bugs

Open an issue using the bug report template in `.github/ISSUE_TEMPLATE/bug_report.md`. Redact personal information from any log you attach.

## License

By contributing, you agree that your contributions are licensed under the MIT License (see `LICENSE`).

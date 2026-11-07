# AutoTeam Studio — focused source patch

This is a modified copy of the user-provided source archive, not a finished or verified Windows release.

## Modified files
- `tests/ui/conftest.py`: put UI temporary workspaces in the short system temp directory (or the `AUTOTEAM_TEST_TEMP` path); retry Windows cleanup after garbage collection. It will still fail visibly if files remain locked.
- `tests/ui/test_production_smoke.py`: pass an explicit unittest discovery path/pattern without nested PowerShell/CMD quotes around the pattern. This needs verification on Windows.
- `src/autoteam_studio/ui/window.py`: display available file paths, exit codes, state and error codes from the *existing sanitized event payload* in the activity feed.
- `pyproject.toml`: declare the Python 3.12 runtime the backend actually supports.

## Not fixed yet
- The core reservation/concurrency test failure requires investigation of the worker's state at the exact second the test fails.
- Fail-safe reporting when the diagnostics store reaches capacity.
- Preserving process logs on failure/timeout.
- Contribution rules for non-Python projects and per-owner attribution after integration repair.
- Windows GUI integration acceptance and Windows executable packaging.

## Testing performed
- Python compileall: passed.
- Local pytest for `tests/test_contracts.py` and `tests/core/test_deadlines.py`: 5 passed.
- Full Windows/PySide6 tests: **not run** in this environment.

## How to test safely on Windows
1. Extract to a new folder, e.g. `C:\Users\Arjun\Projects\AutoTeam_Patched`, rather than overwriting your original worktree.
2. In a terminal in the extracted folder, run `py -m pip install -e ".[dev]"` if the Python environment lacks dependencies.
3. Run `py -m pytest -q --tb=short --maxfail=5`.
4. Share any remaining errors. Do **not** run real Codex accounts yet; this is a local test pass.

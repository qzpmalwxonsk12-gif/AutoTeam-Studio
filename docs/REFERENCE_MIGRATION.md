# Using the earlier working scripts

`reference/legacy_auto_team/team.py`, `dashboard.py`, and `smoke_test.py` are copied from the earlier AutoTeam Live Dashboard package created during this conversation. These are **reference source code**, not a certified ready-to-bundle backend.

Their useful existing behaviors:

- two independent Plus account profiles through `$HOME/.codex-a` and `$HOME/.codex-b`;
- Git worktrees for `a`, `b`, `result`;
- preflight read/write permission checks;
- proposal -> critique -> plan -> parallel implementation -> review -> integration workflow;
- `codex exec --json` streaming to run-specific JSONL event logs;
- corrections for upcoming agent calls through `control.json`;
- checkpoint pause, resume and stop;
- durable reports and run history in `.<project>-autoteam` beside the selected repo;
- bounded call timeouts, refusal to commit common credential files, virtual environment exclusions.

Important caveats to fix/verify:

- `team.py` is a command-line program, not a directly importable desktop backend API.
- `dashboard.py` binds a localhost server and is not the requested native PySide6 app.
- The old coordinator can stop when implementation results have no code changes. New app must display this as failure with explanations.
- A packaged GUI must bundle an app-owned backend runner, not depend on source in `Downloads` or external Python.
- Prior commands have tested Windows sandbox/setup issues: real process execution may fail even when login works. Surface stderr and retain errors.
- Owner may choose to run old and new coordinator side by side, but do not automatically move/delete old run history.

Agent A: read and leverage this code before implementing a backend. Agent B: use the data contracts, not direct parsing of these low-level logs, so UI stays independent of CLI details.

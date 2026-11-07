# Architecture and interfaces

## Preferred stack

- Windows 10/11; Python 3.12 for development; **PySide6** desktop GUI.
- Local disk + JSON/JSONL run artifacts + Git worktrees.
- Codex CLI authenticated separately with `%USERPROFILE%\.codex-a` and `%USERPROFILE%\.codex-b` via each spawned process's `CODEX_HOME`.
- Use an app-owned worker backend, designed for a packaged EXE; do not launch a `.py` located in Downloads.
- The current `reference/legacy_auto_team/team.py` implements `codex exec --json`, two-account worktree setup, debate/plan/implementation/review, correction files, and status JSON. Reuse/port without breaking those behaviors.
- GUI must be responsive: use Qt signal + `QThread`, worker process, or async event dispatch. Prefer separate backend subprocess to support safe termination and GUI restarts.

## Code ownership

See `docs/TASK_DIVISION.md`. `contracts.py` is an immutable starter data contract; Agent A implements core, Agent B implements UI. Both can add tests in owned test trees.

## Stable API (implementation adapters may extend internally)

Shared types in `src/autoteam_studio/contracts.py`:

- `RunSpec`: `project_root`, `prompt`, `revision`, `test_commands`, `max_call_seconds`, `review_rounds`.
- `AgentEvent`: `run_id`, `agent` (`a`/`b`/`system`), `timestamp`, `kind`, `message`, `payload` (serializable).
- `RunSnapshot`: `run_id`, `status`, `stage`, `result_dir`, `error`, `reported_tokens_a`, `reported_tokens_b`.
- `Correction`: `id`, `text`, `delivered_a`, `delivered_b`.

Proposed core facade (actual class naming flexible if agreed plan documents it):

```python
class TeamController:
    def check_dependencies(self) -> dict: ...
    def prepare_project(self, path: Path, create: bool) -> Path: ...
    def start(self, spec: RunSpec) -> str: ...
    def events_since(self, run_id: str, offset: int) -> tuple[list[AgentEvent], int]: ...
    def queue_correction(self, run_id: str, text: str) -> str: ...
    def pause_next_tasks(self, run_id: str) -> None: ...
    def resume(self, run_id: str) -> None: ...
    def stop_at_checkpoint(self, run_id: str) -> None: ...
    def list_history(self, project_root: Path) -> list[RunSnapshot]: ...
```

Agent A must deliver a usable facade; Agent B builds GUI against it and may use a **fake controller** with the exact same method shapes while A works. No frontend should parse raw CLI records; the backend translates them into shared `AgentEvent` objects.

## Events and transparency

Codex event schema can change. Parse known message/tool/usage types conservatively, preserve raw event references for diagnostics, ignore unknown types safely, and never pretend hidden internal reasoning is available. Keep events per actual authenticated account and don't misattribute a reviewer acting in another worktree. Display only user-safe messages; avoid dumping raw auth values/environment variables.

## Process lifecycle

- `start` returns immediately with a run ID; it must not block GUI thread.
- Existing coordinator currently produces its own generated ID after launch; facade may discover it from new run directories / status file, or refactor the runner to output a machine-readable kickoff record.
- GUI shuts down cleanly; on close with active process ask Stop/Keep Running only if the latter is truly implemented; otherwise explicitly explain and stop safely.
- Worker crashes leave readable error logs + run state. Avoid deleting partial Git worktrees.

## Packaging

- Provide `python -m autoteam_studio` for development.
- Provide PyInstaller (or equivalent) build recipe. Include PySide6 dependencies, resources and backend worker in distributable output.
- The built GUI must not require Python installation. External executables VS Code, Git and Codex may remain required; verify them on startup and give installation guidance.
- Avoid `subprocess([sys.executable, 'worker.py'])` in the bundled GUI: in PyInstaller `sys.executable` is the GUI exe, not Python. Either ship separate worker exe, or implement a tested alternative.
- Prefer onedir output in V1, `dist/AutoTeamStudio/AutoTeamStudio.exe`; single-file packaging may be deferred if onedir works.

# V1 acceptance tests / Definition of Done

## A. Free offline automated tests (`py -m pytest -q`)

These must NOT call OpenAI, use ChatGPT tokens, read real `auth.json`, or modify the user's real projects:

1. Shared `RunSpec` validates paths and limits; event types serialize consistently.
2. Env detector correctly reports two present/missing distinct home paths without reading credentials.
3. Selecting an existing safe folder produces an explicit project path; new project creation safely refuses unwanted overwrite.
4. VS Code launcher receives an argument array (including folder names with spaces), no shell interpolation.
5. Mock worker starts A/B and emits independently attributed activity and usage counts.
6. Event parser tolerates known JSONL and unknown event types without crashing or leaking full environment.
7. Proposed correction enters queue, can be delivered individually to A and B, displays delivery status.
8. Pause/stop-at-checkpoint prevents new tasks; labels match behavior.
9. A broken worker leaves an error + persisted partial history; UI does not mark it successful.
10. Result branch / worktree link can be opened in VS Code without changing the user's original branch.
11. GUI starts headlessly (`QT_QPA_PLATFORM=offscreen`) and can render Projects, Live Workspace, Results/History using fake backend.
12. Empty/placeholder prompt disables or rejects Start.

## B. Manual desktop smoke test

1. `py -m autoteam_studio` opens a real, responsive PySide6 window.
2. Open/Create project buttons work. VS Code opens the chosen folder, not the app's source repository by accident.
3. With missing Codex/Git/VS Code, friendly diagnostics appear and Start is disabled.
4. Fake Demo mode shows concurrent A/B activity in separate panes and populates saved history, with zero model calls.
5. Click correction, stop-at-checkpoint and view feedback states in Fake Demo mode.
6. Reopen app and see recent projects and preserved run history.
7. Enable real use only in a disposable project to verify each `CODEX_HOME` is actually selected. Document which actions used Plus tokens.

## C. Packaging verification

1. Provide script / documented command building `dist/AutoTeamStudio/AutoTeamStudio.exe` using PyInstaller (or equivalent).
2. EXE can launch on a compatible machine **without separate Python installed**, though VS Code/Git/Codex remain external prerequisites.
3. Bundle a worker executable or verified suitable alternative (the GUI must not assume a system Python just to invoke its own backend).
4. UI displays missing dependencies instead of instantly crashing.
5. No home-directory auth files included in packaged artifacts.

## Failure criteria

Any of these makes V1 incomplete: no desktop GUI, no genuine two-account backend integration, fabricated agent messages, missing file-based history, no user-selectable project, interface freezing on subprocess calls, no safe stop/correction semantics, secret credential capture, or treating untested output as passed.

## Finish report

Report exact pytest command and actual result, smoke-test observations, EXE file location (only if it exists), project directory, result branch, open known limitations, and anything needing owner action. Never invent a success or build path.

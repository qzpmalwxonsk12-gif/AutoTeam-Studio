# Parallel ownership and integration contract

## Agent A — Core/worker (owns these paths)

`src/autoteam_studio/core/**`
`src/autoteam_studio/worker/**`
`tests/core/**`

Build:

- dependency/account-home/CLI detection;
- safe project folder and Git validation/initialization;
- VS Code launch helper or backend implementation;
- event ingestion from both agents and persisted run discovery;
- app-owned, nonblocking two-agent coordinator/legacy-engine port;
- correction queue, checkpoint controls, bounded timeouts, failure persistence;
- fake event backend for tests;
- run/test/report result mapping;
- packaged worker process integration.

## Agent B — GUI/UX (owns these paths)

`src/autoteam_studio/ui/**`
`src/autoteam_studio/__main__.py`
`tests/ui/**`
`docs/USER_GUIDE.md`

Build:

- working PySide6 main window and project picker/creator;
- dependency/status visual indicators;
- prompt composer and Start/Revision controls;
- two separately labeled, scrollable live activity panes;
- centralized phase timeline / saved decisions;
- correction, pause, resume, stop-at-checkpoint controls;
- results/history and open in VS Code actions;
- configuration persistence and user-friendly errors;
- GUI tests using a fake facade (no Codex usage).

## Shared files protected during parallel phase

`src/autoteam_studio/contracts.py`, `pyproject.toml`, `AGENTS.md`, `MASTER_PROMPT.md`, `docs/ARCHITECTURE_AND_CONTRACTS.md`, `reference/**`, repository root launcher scripts.

Agents should not edit these in parallel. Any required changes go in a short handoff note and get reconciled at the integration stage. Do not let two agents independently create incompatible contract definitions.

## Integration ownership

The result branch integrates both agent trees. Integration should:

1. Reconcile imports and dependencies in shared bootstrap files.
2. Ensure GUI starts and fake backend runs entirely without a Codex login.
3. Test real CLI command construction with stubs; do not invoke real models during pytest.
4. Build package recipe; validate EXE startup when build tools available.
5. Run all acceptance tests; state unsupported functionality explicitly.

## Work quality bar

Both agents **must produce actual implementation files and their own tests**. A final report with no source changes, no real GUI, or mocked-only production mode is not a successful V1.

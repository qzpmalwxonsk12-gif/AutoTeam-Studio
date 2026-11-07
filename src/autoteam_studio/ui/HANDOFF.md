# Task B handoff

Only UI-owned paths, `__main__.py`, `tests/ui`, and `docs/USER_GUIDE.md` were edited.
No A-owned implementation, shared contract, metadata, root launcher, or reference
source was changed. No deployment, commit, real Codex call, or credential read was
performed.

## Implemented

- Runnable native PySide6 window with Projects, Live workspace, Results/history,
  and Settings; backend absence is visible and blocks operations.
- Sole production backend import boundary: `autoteam_studio.core.api` with
  `TeamController(ControllerSettings(...))`. Shared `RunSpec` remains unchanged.
- One background work lane plus one reserved control lane. Each accepts only one
  outstanding operation, including signal delivery; at most one poll batch can
  await the GUI. No facade subprocess work occurs on the GUI thread.
- Coherent `poll()` rendering with controller/project epoch, run-selection epoch,
  run ID, monotonic generation, matching details generation, and cursor checks.
  Reads of separate snapshot/details endpoints are not combined.
- Independent A/B panes and timeline, each capped at 1,000 records; unknown kinds
  use plain text. Backend usage/coverage is authoritative.
- Exact inspection presentation and explicit authorization grant; revisions use
  `start_revision(spec, source_run_id)`. Failed Start errors select their run ID.
- Checkpoint requests, correction delivery states, active deadlines, startup
  reconciliation, artifacts, result opening, immutable settings replacement,
  persisted recent projects, and stop-before-close behavior.

## Integration blockers and decisions to reconcile

1. This checkout has no `core/` or `worker/` implementation. Production integration
   cannot be exercised yet. The production-fake smoke skips only absent API (or
   unsupported OS/Python/missing Git), not facade or worker defects.
2. A's validated fixtures were unavailable for usability review and reuse.
   `tests/ui/double.py` is explicitly provisional, with the agreed exact keys.
   Replace its fixture provider with A's validated provider in B's exclusive
   integration window. No fixture export name was defined in the agreed plan.
3. `TeamController(settings)` is the constructor used here; confirm A's constructor.
   The smoke currently authorizes `"<Python 3.12>" -m unittest discover` explicitly
   in fake mode. Reconcile with A's app-owned fixture-test authorization/provider
   when available. Never bypass real test authorization to make this pass.
4. `reconcile(None)` is expected to enumerate unfinished indexed runs (and may
   return terminals), allowing close to prove no active run remains. Operation
   deadlines are A's responsibility; Python facade threads cannot be force-killed
   safely. A must honor the agreed budgets.
5. Shared metadata still says Python >=3.11. Serial integration must set
   `>=3.12,<3.13` and add the GUI/worker packaging recipe and resources. No metadata
   edits or EXE build were made during B's assignment.

## Verification

Tests cover view creation, prompt/dependency rejection, preparation, exact test
authorization/cancellation, separate activity, unknown kinds, bounded bursts,
GUI responsiveness, reserved controls/backpressure, correction uncertainty,
requested/acknowledged checkpoints, explicit revision sources, stale responses,
failed launch selection/history, result/artifact opening, settings invalidation,
startup recovery and stop-before-close. A subprocess test runs the real module
entry point with an offscreen GUI and a deliberately unavailable backend.

Initial ordinary pytest temporary-directory creation failed with Windows access
denied: Python 3.12 mode-0700 directories excluded the restricted token. UI fixtures
now create disposable test workspaces with inherited permissions under
`tests/ui/.test-work`, removing only their validated child paths afterward.
Pytest cache writes may also fail in this environment; `-p no:cacheprovider` avoids
that unrelated cache operation. Inaccessible temporary directories left by those
initial pytest attempts are not source artifacts and were not permission-modified.

Automated results are recorded in the final task response. No visible desktop
observation, packaged verification, original-checkout preservation through the
missing production worker, or real-account verification is claimed.

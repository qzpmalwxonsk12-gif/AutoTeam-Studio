# Task A recovery handoff

Recovery continued existing core/worker code. Ownership remains limited to
`core/**`, `worker/**`, and `tests/core/**`. No shared contracts, UI files,
reference code, account configuration, or repository commits were changed.

## Production facade and offline worker

UI imports production interfaces from `autoteam_studio.core.api`:
`TeamController`, `ControllerSettings`, `ControllerError`, all supplementary
TypedDicts, `validate_response`, `fake_demo_commands`, `poll_fixture`, and
`representative_fixtures`. Shared contracts remain unchanged.

The facade supplies project preparation, dependency detection, exact test
inspection/grants, new runs and explicit revisions, coherent polling, durable
controls, indexed history/reconciliation across storage setting changes, and
VS Code/artifact opening. Call these on the UI background adapter.

Fake mode uses the actual app-owned worker and independently labeled A/B CLI
processes through real pipes, Windows suspended launch/Job containment, Git
worktrees, source and tests from both owners, structured fixture reviews,
integration, explicitly authorized project tests, pinned SHA checks, and
durable reports. It never invokes Codex or reads account credentials.
All fake activity and reviews are labeled fixtures, not real model evidence.

To configure an offline demonstration, prepare an empty project with
`prepare_project(path, True)`, obtain `fake_demo_commands()`, inspect those
commands, present the notice, grant the exact inspection ID, and pass the
commands in `RunSpec.test_commands`. Empty commands fail with
`tests_not_configured`; fake mode does not implicitly authorize test execution.

Fake scenario prompt markers: `[fake:crash]`, `[fake:cross-repair-a]`,
`[fake:cross-repair-b]`, `[fake:integrated-repair]`. Integrated repair requires
`review_rounds >= 2`. Pause/stop operate at checkpoints. Corrections acknowledge
prompt submission independently per account; uncertain submissions are not
retried. `start_revision` requires a nonempty source ID and always takes that
completed result SHA as the new base, regardless of `spec.revision`.

Validated fixtures include queued/running/paused/completed/failed/stopped,
uncertain correction delivery, missing usage, and launch failure. Live UI reads
must use `poll`, whose cursor is a byte offset, rather than combine independent
read methods. A batch contains at most 200 events and 256 KiB of encoded records.

## Recovery changes

- Invalid handoff tokens/protocols cannot mutate run state or reservations.
- Credential filename scanning examines every introduced commit tree, including
  merge commits, without reading credential blobs.
- Crash recovery verifies original-checkout/configuration preservation and
  reports violations without restoring or deleting user work.
- Artifact opening rejects changed path indirection, including an evidence
  directory redirected through a junction.
- Poll validation rejects mixed generations, relative worktrees, unsupported
  schema versions, and invalid supplementary UTC timestamps.
- Persisted event payloads are allowlisted and sanitized again at read time;
  missing/truncated published logs fail explicitly.
- Facade operation budgets propagate to nested subprocesses, guards, storage
  scans, and checkout hashing. Cleanup has a separate five-second allowance
  after timeout so contained processes and failed-launch evidence are handled.
  This allowance means strict total elapsed-time ceilings from the final plan
  are not fully met. OS-level blocking file/Popen calls cannot be preempted by
  these cooperative deadlines.

## Remaining blockers and integration work

**Real backend is implemented.** Real Start validates distinct credential-file
locations without reading their contents. The app-owned worker runs local
`codex login status` for each home and rejects missing/API authentication. Each
model call receives its own sanitized environment and `CODEX_HOME`, plus
`forced_login_method="chatgpt"`, the OpenAI provider, approval policy `never`,
explicit read-only/workspace-write sandbox, no additional writable roots and
no sandbox network access. Windows selects the unelevated sandbox and never
automatically requests setup/admin access or falls back to unrestricted mode.
Unsupported CLI policy, sandbox failures and expired logins fail durably.

Independent proposals/peer critiques feed one synthesized plan, B's challenge,
and A's final shared ownership plan. That output reaches both implementations.
Real paths are staged after credential-name/link checks, independently committed,
cross-reviewed, optionally repaired, and integrated into the result worktree.
Structured Codex reviews use an app-owned JSON schema and SHA validation; only
authorized passing tests and both final reviews permit completion. Review and
test output is handed to bounded repair calls. Fake markers/resources remain
exclusive to fake mode. No real-account verification or model calls were run
during this fix; production sandbox capability remains an explicit disposable
project smoke-test step.

Policy references: https://learn.chatgpt.com/docs/config-file/config-reference
and https://learn.chatgpt.com/docs/developer-commands?surface=cli . Account homes
must not overlap the project or run storage. The CLI can verify authentication
method, but the app does not inspect credential identities to establish that
the two directories contain different subscriptions.

Further plan gaps: no emergency disk reserve or separate worktree capacity
preflight; malformed event diagnostics are bounded error events rather than
durable diagnostic artifacts; allocation-time persistence failures before
index publication are not fully recoverable; on recovery, unpublished event
tails are recorded as byte-count metadata rather than preserved content.
Contribution classification currently supports meaningful Python AST changes
and fails ambiguous/non-Python contributions. Fake workflow planning stages
are fixtures and do not establish real independent proposals or reviews.

Shared packaging metadata still declares Python >=3.11. Serial integration
must change it to `>=3.12,<3.13`. PyInstaller is available in the inspected
Python 3.12 environment, but building artifacts is outside this recovery's
owned paths. No EXE, packaged verification, visible desktop observation, or
GUI-to-production smoke-test result is claimed.

Worker entry point is `src/autoteam_studio/worker/__main__.py`. The future
integration build can produce the required worker path with:

```powershell
py -3.12 -m PyInstaller --clean --noconfirm --onefile --console --paths src --name AutoTeamStudioWorker --distpath dist/AutoTeamStudio/worker src/autoteam_studio/worker/__main__.py
```

Build the GUI onedir first, then place the worker at
`dist/AutoTeamStudio/worker/AutoTeamStudioWorker.exe` so the GUI build does not
overwrite it. This recipe is unexecuted and requires packaged fake testing on
Windows without separate Python. Worker runtime dispatches `--fake-cli` and
`--fake-test` internally when frozen. Include app modules and standard-library
fake resources; never add account homes to the bundle.

Tests use temporary Git repositories under `tests/core/.r` with inherited
Windows ACLs because mode-0700 pytest temp directories excluded this execution
sandbox SID in the preceding implementation. Previous test directories and
their worktrees are preserved. No automatic cleanup of those projects is done.

## Blocking-finding fix verification

`py -3.12 -m pytest -q tests/core -p no:cacheprovider` — **50 passed in
351.36s**. The six new real-mode tests mock all Codex processes and cover
account-specific environments, ChatGPT-only authentication rejection, Windows
sandbox policy, proposal/plan handoffs, cross-review repair, real-path staging
and result integration, authorized offline tests, SHA-bound final reviews,
and authentication-failure history. The initial synchronous test launcher
exceeded Start's deadline; the corrected mock launches a background worker
thread, matching the production process boundary.

`py -3.12 -m compileall -q src/autoteam_studio/core src/autoteam_studio/worker`
and `git diff --check` passed. No real Codex inference, repository commit,
deployment, UI edit, or shared-contract edit was performed.

## Verification

The recovered pre-change suite passed 32 tests. Additional regression coverage
includes handoff rejection, preservation checks on crash, redirected evidence
paths, schema consistency, event read sanitization, and nested absolute
deadlines with real subprocess cleanup, integrated repair limits, and source
mutations after test authorization. Final full-suite command:
`py -3.12 -m pytest -q tests/core -p no:cacheprovider` — **44 passed in 300.37s**.
Compilation
via `py -3.12 -m compileall -q src/autoteam_studio/core src/autoteam_studio/worker`
passed. No automated tests invoke real Codex.

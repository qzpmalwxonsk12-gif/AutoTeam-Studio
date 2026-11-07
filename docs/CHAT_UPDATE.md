# Conversation-first update

## Implementation stages

1. Add a durable group-chat backend isolated from the Git/build coordinator. Two concurrent replies per user message, shared recent context, cancellation, per-agent delivery/error state, saved conversation selection, and offline workers.
2. Require structured decisions at proposal/final-plan boundaries. Stop on greetings, missing requirements, cancelled plans, or empty implementations. Retain corrections across later stages.
3. Replace the dense workspace with a shared bubble thread and one visible composer. Add Just chat phone layout, light/dark palettes, expandable technical activity, and reviewable project briefs.
4. Verify simulated source workflows, regression tests and rendered layouts, then rebuild both Windows executables and repeat the packaged chat/build smoke.

## Behavior

Sending a chat message does not call `TeamController.start`, create a worktree, run a test, or stage a file. A build suggestion requires successful structured responses from both bots. The user reviews the brief and confirms the exact project/prompt/test policy before development starts. While a build is active the composer sends guidance into the existing correction queue. Completed project summaries become conversational context.

The Just chat switch suppresses build suggestions in both the backend and UI. Direct calls to the UI build action also refuse this mode. The build worker independently validates task decisions so an accidental greeting cannot be forced through implementation.

## Validation

Full suite: 113 passed. Final UI suite: 37 passed, including two additional regressions (115 tests currently collected). The packaged chat/build smoke and bundled-worker cancellation check passed. See COMPLETION_REPORT.md and dist/20261010-183519/build-manifest.json for exact evidence. Screenshots and offline smoke artifacts are retained under `artifacts/` and the timestamped `dist/` build.

No real Codex development or chat calls were used in automated testing. No credentials or existing project histories were edited. The existing running application is not stopped or replaced in place; the launcher points to the new version after verification.

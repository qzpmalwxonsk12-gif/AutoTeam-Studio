# Product requirements — V1 (Windows-only MVP)

## User journey

- Launch AutoTeam Studio. The app opens immediately even if Codex is unavailable; it shows missing dependency messages.
- User can pick an existing folder or create a new folder under a user-selected parent.
- The app displays selected project name/path and an explicit **Open in VS Code** control. Optionally auto-open after selection, with an app setting to disable auto-open.
- If repo has no `.git`, offer one-click safe Git initialization. If uncommitted changes exist, block Start and explain committing/stashing (do not auto-stash or reset).
- User types a task prompt, clicks **Start AI Team**. Reject empty/placeholder prompts.
- UI displays two separately labeled agent panels and a unified stage timeline. Source must be real Codex `--json` records and report files.
- User can queue a correction during a run. Show pending/delivered-to-A/delivered-to-B states. Stop-at-checkpoint is explicit.
- App indicates run success only after non-empty implementation, merge, final reviews, and configured tests pass. Otherwise present limitations.
- User can open resulting worktree in VS Code, inspect full final report and list all history entries.

## Primary screens

1. **Projects**: create/open/recent directories, dependency status, selected workspace, VS Code open.
2. **Live workspace**: prompt, Start, stage timeline, Agent A/B activity, token metrics, corrections, pause/stop controls.
3. **Results/history**: run list, final status, reports/links, errors, open result folder.
4. **Settings (simple)**: Codex A/B home paths, Codex executable, VS Code executable, max seconds per turn, review rounds, optional test command, auto-open VS Code toggle.

## UX details

- Real labels: "Last reported activity", "Waiting for Codex", "Agent A working", "Agent B working", "Stopped at checkpoint".
- Explicit difference between pending correction and delivered correction.
- Clear error if app starts an active run while another is already running for same project; reject duplicate concurrent runs.
- New project and running result are separate locations because of Git worktrees.
- Accessible control text and keyboard navigability; non-blocking app.
- Persist preferences and recent folders locally under a standard Windows app-data directory; do not put global settings in project Git.
- Show save paths of run reports and worktrees, plus buttons to open using VS Code / Explorer.

## Non-goals (defer to V2)

- Instant mid-turn preemption, strong resumability for an arbitrarily interrupted model call, account usage-remaining meter, remote/cloud orchestration, arbitrary project deployment, embedded IDE, chat with hidden thoughts, autonomous GitHub PRs, plugin marketplace.

## Requirements around usage

- Running or previewing the desktop GUI must not use Codex allowance.
- Starting a real run clearly indicates it will use both subscriptions.
- A **Fake demo** mode uses fixture events and artificial delays only (no Codex calls) so every screen/control can be tested freely.
- Max 1 active team run per project; bounded call timeouts and retries; status persistence after failure.

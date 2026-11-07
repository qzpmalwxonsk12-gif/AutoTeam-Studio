# AutoTeam Studio user guide

## Talk to your team

The app opens on **Team chat**. Click **Connect accounts**, then type a message and press **Enter** or **Send**. **Shift+Enter** adds a new line. A project folder is not needed for conversation. Both bots receive the same recent conversation, including each other's previous replies, and each replies once. There is no automatic back-and-forth loop.

The **Just chat** toggle gives you a compact, phone-like group conversation and hides project build actions. Even a message saying “build an app” stays conversational in this mode. **Soft light** and **Quiet dark** change the whole application's palette; your choices are saved.

With Just chat off, a concrete software request can produce **Review brief**. Review and edit the brief, select a project and tests, then confirm **Build from this message**. You can also use that build button directly with a task in the composer. Sending a greeting never starts development. Agents may ask for clarification instead of producing a brief. A brief requires successful replies from both agents; it never starts work automatically.

Each chat message uses one call per connected account, with no hidden follow-up calls or automatic retries. **Cancel replies** interrupts the app-owned chat processes. Each bot's bubble shows waiting, sending, awaiting reply, delivered, cancelled, failed or interrupted. A delivered reply from one bot is retained if the other fails. Send a new message to continue after a failure; old messages are retained.

Use the conversation dropdown to reopen saved chats. **New chat** starts another conversation without deleting earlier ones. The last 200 messages are displayed; the full saved JSON history remains on disk. Recent delivered messages, bounded to about 32,000 characters, are supplied to both agents. The app adds project task/outcome summaries to the conversation; chat cannot inspect the source files itself.

Chat runs in an app-owned directory outside the project with a read-only sandbox. Shell/exec tools, apps, web search and subagents are disabled through invocation-local configuration. It ignores the account's user config for that invocation, preserving the saved authentication home. It does not use API keys or edit account configuration. These flags are documented in the [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) and [CLI reference](https://learn.chatgpt.com/docs/cli/reference). A current Codex CLI supporting `--ignore-user-config` and `--ephemeral` is required; incompatible versions fail visibly. The real-account path is verified with mocked processes, not paid live calls.

## Choose a project

Use **Project > Open project** for any local project folder. Selection does not start agents and does not require the repository to be clean. Use **Create project** to choose a parent directory and name a new folder; the app creates an empty local Git baseline.

An existing folder without Git, an unborn repository, or a folder with pending changes can use **Review and save Git baseline**. The dialog shows files included and tracked deletions before you approve a local commit. A content check rejects a stale preview. `.gitignore` is respected; credential-like filenames and linked paths are blocked. Existing histories and files are never reset, stashed or deleted. A folder inside another repository gets its own repository only after your confirmation; the parent repository is not used implicitly.

Finish an existing merge/rebase/cherry-pick in VS Code before preparing a baseline. Start checks cleanliness again. Work is performed in separate A, B and result worktrees outside the selected checkout.

## Connect the saved accounts

The two account homes default to `%USERPROFILE%\.codex-a` and `%USERPROFILE%\.codex-b`. Browse to other saved homes in Settings if needed, then save. **Check / connect saved accounts** runs only the CLI's local saved-login status check. It does not start development, open a login flow, alter authentication settings or reveal credential contents.

The interface distinguishes a configured credential file from a checked ChatGPT login. This local check cannot establish whether a token has expired at the service or whether the two directories belong to distinct subscriptions. A real worker checks again before starting. Account setup and expired-login renewal remain external to this application.

The check uses the documented [`codex login status` command](https://learn.chatgpt.com/docs/developer-commands?surface=cli), which reports the authentication mode. No real account verification or inference was performed during automated development testing.

## Configure tests

In Settings, enter one command per line or use a preset, click **Save settings for new launches**, then **Inspect and authorize saved test commands**. Authorization applies to the selected project, executable arguments, working directory, timeout and environment policy. Changing the policy requires a new authorization.

Examples:

| Project | Command |
| --- | --- |
| Python | `python -m pytest` (choose your project interpreter when necessary) |
| JavaScript | `node --test` or `npm test` |
| Rust | `cargo test` |
| Go | `go test ./...` |
| .NET | `dotnet test` |
| Other toolchains | Their installed native test executable and arguments |

The npm Windows installation shim is resolved to Node and npm-cli.js; arbitrary shell wrappers and shell operators are rejected. Tests execute code written by the agents in the result worktree, outside the Codex sandbox. The confirmation dialog shows the exact resolved commands. Build/cache output should be in `.gitignore`; unexpected deliverable changes invalidate the test evidence.

Source/test verification supports Python plus common JavaScript/TypeScript, Java/Kotlin, C/C++, C#, Go, Rust, Ruby, PHP, Swift, Dart, web UI, SQL and scripting file extensions. Non-Python recognition is structural rather than a compiler or semantic proof. Unknown layouts/extensions or documentation-only contributions can be rejected. Both agents must retain meaningful source and test contributions, with disjoint owned paths. Acceptance still requires passing configured commands and both SHA-bound final reviews.

For a free demonstration, choose the **Offline demo** test preset. This sets the backend to `fake` and fills the app-owned test command. Save and authorize, then confirm a task in a dedicated demo project. Everything is clearly labeled FAKE.

## Start, communicate and review

Enter the task in the Team chat composer. **Build from this message** opens a confirmation with the project, exact prompt, backend and test commands. Cancel performs no work. Connecting accounts, selecting a project and editing the composer never start a run. The initial proposals and final plan use structured decisions; no-task and clarification decisions stop the coordinator before implementation. Empty implementations also stop cleanly instead of attempting an empty Git commit.

The agents exchange independent proposals, critiques and a shared plan, implement in separate worktrees, cross-review, integrate and review the tested result. The shared thread shows the discussion. Expand **Work details** for activity panels with surfaced messages, changed paths, commands, exit status and bounded sanitized command output. They do not expose hidden reasoning. Token counters show reported usage, not remaining subscription allowance.

While a project run is active, the same composer becomes **Send guidance** for further instructions. A saved message appears as pending, submitting, delivered or delivery uncertain for each account. Delivered means the prompt was submitted; it does not prove compliance. Messages wait for the next eligible call and do not interrupt an ongoing call. Uncertain delivery is never silently retried. A message sent too late may remain pending when a run finishes; it is retained in history and the report. Use a revision for further changes.

**History** shows all indexed runs, worktree locations, verification gates and report artifacts. Select an artifact before opening it. Reports include test/review results and pending messages. **Open result worktree in VS Code** opens the actual deliverable; the original checkout is retained. To revise a completed result, select it from history, enter a revision prompt in Team chat, then confirm **Revise completed project**. Its recorded result commit becomes the revision's base.

## Pause, stop, resume and failures

**Pause** waits for active calls to finish, then pauses before more work. **Resume** continues the same worker without repeating completed stages.

**Stop** ends the worker safely. In History, **Resume stopped run from checkpoint** confirms continuing the saved task/backend. Completed calls, commits, merges and test/review operations are reused from a durable journal. The original checkout, stopped worktrees and saved operation records must still match. New settings and a newly typed task do not silently replace the stopped run's settings or original task.

Crashes, timed-out writes, edited worktrees and older runs without the new journal are not automatically replayed. Their files, logs and history are preserved for inspection. Failure diagnostics include the account, stage, command, deadline, last reported activity and bounded sanitized partial output where available. Inspect the retained worktree in VS Code before deciding how to continue. There is no force-reset or automatic unlimited retry button.

Closing the application with active work requests checkpoint stop and waits. This can take until an active call's deadline. Reopen and select the stopped history entry to resume. Detached background continuation is not offered.

## Storage and distribution

Preferences and the global run index live under local application data in `AutoTeamStudio`. Conversations live in `AutoTeamStudio/core/conversations` as local JSON files. They are not encrypted; do not paste secrets into chat. Cancelled/interrupted messages are retained and never silently retried. By default, run files and worktrees are in a sibling `.PROJECT-autoteam-HASH` directory; a custom storage root can be selected. A storage root cannot be changed while indexed runs remain unfinished. No history-deletion control is provided.

Distribute the entire generated `AutoTeamStudio` folder, including `_internal` and `worker`. The GUI and worker include Python; Git, VS Code, Codex and project-specific tools remain external. Use the launch/build double-click files in the source checkout for normal operation and rebuilding.

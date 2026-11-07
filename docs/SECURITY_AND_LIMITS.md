# Security, privacy, and limits

- User explicitly starts each run. Do not contact Codex during mere app launch, project browsing, viewing old logs, or Fake demo mode.
- Chat is a separate path from project runs. A user-sent message calls each account once in an isolated app-owned folder with a read-only sandbox and invocation-local restrictions. The chat service has no dependency on Git or the build coordinator. It cannot automatically start a project run; suggested briefs require explicit review/start confirmation. Just chat mode suppresses build suggestions and every UI build entry point.
- Chat starts fresh ephemeral CLI calls with the same bounded recent shared transcript, not permanently connected model sessions. It does not inherit user `config.toml` tools or model settings. Saved ChatGPT authentication is still selected via each account home. Old/incompatible CLIs fail visibly. Real CLI behavior remains untested with live accounts.
- Chat replies are cancellable app-owned processes. User cancellation or process failure preserves messages; interrupted replies are not automatically retried. Saved conversation files are local, unencrypted JSON. The UI displays the latest 200 messages, sends a bounded recent context, and retains earlier history on disk.
- Project proposals and the final plan must contain valid structured task decisions. No-task/clarification decisions stop before implementation. A no-task stop has no resumable checkpoint; starting a new confirmed task is required. Owner corrections remain in subsequent stage prompts.
- Require separate CODEX_HOME for accounts A/B; don't inspect the contents of their auth tokens. Do not commit `auth.json`, `.env`, `.pem`, `.key`, `.venv`, logs containing tokens or application cache.
- Restrict creating new projects to the selected user-writable path, prevent path traversal, and warn before interacting with an existing nonempty directory. Never recursively delete projects or old worktrees without explicit separate confirmation.
- Never execute untrusted raw `shell=True` commands constructed from project name/path/prompt. For VS Code launch use a list such as `subprocess.Popen([code_executable, str(path)])`.
- Never automatically deploy, push, purchase services, edit system settings, request admin access, or run dangerous operations outside user-selected workspace. An explicitly confirmed baseline can make a local commit on the selected branch; agent implementation commits are confined to app-owned worktrees.
- New runs refuse dirty Git repositories rather than silently reset or stash user work.
- Use Git worktrees for isolation and keep result branch separate until owner approves it.
- Tests executed outside Codex sandbox can run arbitrary project code. Document that risk; require owner to opt in to test commands entered into Settings. Safe fake tests must have no real CLI call.
- GUI exposes no network listener by default. If reusing localhost dashboard, bind 127.0.0.1 and enforce same-session authorization for control calls. Do not log or display secrets.
- Corrections, pause, and stop-at-checkpoint are not instant interrupts. GUI labels must not misrepresent capabilities.
- Token totals from streamed CLI events are estimates of reported model usage; they are not Plus quota remaining. Never guarantee precise quotas or costs.
- Long model calls can last; per-call timeout + visible last activity + safe stop control required; don't mark waiting for network/model as failure based solely on low CPU.
- Automatic review rounds capped by settings; retry loops bounded and failure clearly recorded.
- If a process is interrupted, protect completed files/logs/worktrees and show a recovery hint instead of restarting the whole project automatically.

"""App-owned Codex launch policy. Never read or copy credential contents."""
from pathlib import Path
import os

from .process import execute
from .safety import resolve_launcher, sanitized_environment
from .types import ControllerError


def configuration():
    overrides = [
        'forced_login_method="chatgpt"', 'model_provider="openai"',
        'approval_policy="never"', 'sandbox_workspace_write.writable_roots=[]',
        'sandbox_workspace_write.network_access=false',
        'sandbox_workspace_write.exclude_tmpdir_env_var=true',
        'sandbox_workspace_write.exclude_slash_tmp=true',
    ]
    if os.name == "nt":
        # No elevation/setup or fallback to an unrestricted process.
        overrides.append('windows.sandbox="unelevated"')
    return [arg for value in overrides for arg in ("-c", value)]


def resolve_accounts(settings, project):
    command = resolve_launcher(settings.codex_executable, "codex")
    if not command:
        raise ControllerError("codex_unavailable", "Install Codex CLI or select its executable.")
    homes = {who: getattr(settings, f"account_{who}_home").resolve() for who in ("a", "b")}
    if homes["a"] == homes["b"] or (homes["a"].exists() and homes["b"].exists() and homes["a"].samefile(homes["b"])):
        raise ControllerError("account_homes_not_distinct", "Select distinct account homes.")
    for home in homes.values():
        if not home.is_dir() or not (home / "auth.json").is_file():
            raise ControllerError("account_not_configured", "Log in to both account homes externally before Start.")
        if home.is_relative_to(project) or project.is_relative_to(home):
            raise ControllerError("account_home_unsafe", "Account homes must be outside the project workspace.")
    return command, homes


def verify_login(command, home, folder, before_resume=None):
    # Local CLI status only; no inference and no credential output is persisted.
    code, out, err = execute([*command, *configuration(), "login", "status"], folder, 10,
                            env=sanitized_environment(home), before_resume=before_resume)
    if code or "logged in using chatgpt" not in (out + err).lower():
        raise ControllerError("chatgpt_login_required", "Codex requires a saved ChatGPT login in each selected home; API authentication is unsupported.")


def invocation(command, folder, writable, schema=None):
    args = [*command, *configuration(), "exec", "--json", "-C", str(folder),
            "--sandbox", "workspace-write" if writable else "read-only"]
    if schema is not None:
        args += ["--output-schema", str(schema)]
    return [*args, "-"]


def chat_invocation(command, folder, schema):
    """Conversation runs outside projects and does not inherit account tool config.

    These overrides are invocation-local. No saved account configuration is edited.
    See https://learn.chatgpt.com/docs/config-file/config-reference .
    """
    overrides = [
        'features.shell_tool=false', 'features.unified_exec=false',
        'features.shell_snapshot=false', 'features.apps=false',
        'features.code_mode.enabled=false', 'agents.enabled=false',
        'web_search="disabled"', 'project_doc_max_bytes=0',
        'model_reasoning_effort="low"',
    ]
    return [*command, *configuration(),
            *[arg for value in overrides for arg in ("-c", value)],
            "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
            "--json", "-C", str(folder), "--sandbox", "read-only",
            "--output-schema", str(schema), "-"]

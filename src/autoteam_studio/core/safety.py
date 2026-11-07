from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys

from .types import ControllerError

SECRET_NAMES = {"auth.json", "credentials.json", ".env", "id_rsa", "id_ed25519"}

def prohibited_path(path: str) -> bool:
    parts = path.replace("\\", "/").lower().split("/")
    return any(p in SECRET_NAMES or p.startswith(".env.") or p.endswith((".pem", ".key", ".p12"))
               or p in (".ssh", ".codex", ".codex-a", ".codex-b") for p in parts)

def sanitize(text: str) -> str:
    text = str(text)
    # Exclude whole credential-like lines, not only the first word of a value.
    text = re.sub(r"(?im)^.*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization\s*:|bearer\s|auth\.json).*$",
                  "[redacted sensitive record]", text)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b", "[redacted]", text)
    return text.replace("\x00", "")

def sanitized_environment(account_home: Path | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not any(
        marker in k.upper() for marker in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "BILLING", "OPENAI", "CODEX", "AZURE_OPENAI")
    )}
    env["GIT_TERMINAL_PROMPT"] = "0"
    for k in list(env):
        if k.startswith("GIT_") and k != "GIT_TERMINAL_PROMPT":
            env.pop(k)
    if account_home is not None:
        env["CODEX_HOME"] = str(account_home)
    return env

def check_local_volume(path: Path) -> None:
    if os.name != "nt":
        return  # Portable offline tests; production real backend is Windows-only.
    path = path.resolve()
    anchor = str(path.anchor)
    if not anchor or anchor.startswith("\\\\"):
        raise ControllerError("unsupported_filesystem", "Choose a local fixed NTFS volume.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if kernel.GetDriveTypeW(ctypes.c_wchar_p(anchor)) != 3:
        raise ControllerError("unsupported_filesystem", "Choose a local fixed NTFS volume.")
    fs = ctypes.create_unicode_buffer(64)
    if not kernel.GetVolumeInformationW(ctypes.c_wchar_p(anchor), None, 0, None, None, None, fs, 64) or fs.value != "NTFS":
        raise ControllerError("unsupported_filesystem", "Storage identity requires a fixed NTFS volume.")

def identity(common: Path) -> str:
    common = common.resolve(strict=True)
    check_local_volume(common)
    stat = common.stat()
    if not stat.st_ino:
        raise ControllerError("identity_unresolved", "Cannot establish repository filesystem identity.")
    return hashlib.sha256(f"{stat.st_dev}:{stat.st_ino}".encode()).hexdigest()

def resolve_launcher(path: Path | None, name: str) -> list[str] | None:
    found = str(path) if path else shutil.which(name)
    if not found and os.name == "nt":
        found = shutil.which(name + ".cmd")
    if not found:
        return None
    target = Path(found).resolve()
    if target.suffix.lower() in (".cmd", ".bat", ".ps1"):
        # Recognize by installation layout, never execute the wrapper text.
        if name in {"codex", "npm"}:
            entry = target.parent / ("node_modules/@openai/codex/bin/codex.js" if name == "codex" else "node_modules/npm/bin/npm-cli.js")
            node = target.parent / "node.exe"
            node_path = str(node) if node.is_file() else shutil.which("node")
            if target.stem.lower() == name and entry.is_file() and node_path:
                return [str(Path(node_path).resolve()), str(entry.resolve())]
        if name == "code":
            exe = target.parent.parent / "Code.exe"
            if target.stem.lower() == "code" and exe.is_file():
                return [str(exe.resolve())]
        raise ControllerError("unsupported_launcher", "Use a native executable or a recognized Codex/VS Code installation shim.")
    return [str(target)]

def windows_arguments(command: str) -> list[str]:
    """CommandLineToArgvW-compatible quoting, without shell interpretation."""
    if not command.strip() or any(c in command for c in "&|<>\r\n\x00`"):
        raise ValueError("Empty commands, shell operators, and multiline commands are unsupported")
    args, token, quoted, started, i = [], [], False, False, 0
    while i < len(command):
        c = command[i]
        if c in " \t" and not quoted:
            if started:
                args.append("".join(token)); token = []; started = False
            i += 1; continue
        started = True
        if c == "\\":
            end = i
            while end < len(command) and command[end] == "\\":
                end += 1
            n = end - i
            if end < len(command) and command[end] == '"':
                token.extend("\\" * (n // 2))
                if n % 2:
                    token.append('"')
                else:
                    quoted = not quoted
                i = end + 1; continue
            token.extend("\\" * n); i = end; continue
        if c == '"':
            if quoted and i + 1 < len(command) and command[i + 1] == '"':
                token.append('"'); i += 2; continue
            quoted = not quoted
        else:
            token.append(c)
        i += 1
    if quoted:
        raise ValueError("Unbalanced command quotes")
    if started:
        args.append("".join(token))
    return args

def resolve_tests(commands: tuple[str, ...]) -> list[list[str]]:
    result = []
    for command in commands:
        args = windows_arguments(command)
        executable = Path(args[0])
        resolved = str(executable.resolve()) if executable.is_absolute() and executable.is_file() else shutil.which(args[0])
        if not resolved:
            raise ValueError("Test executable was not found; use an absolute native executable")
        exe = Path(resolved).resolve()
        if exe.stem.lower() == "npm":
            launch = resolve_launcher(exe, "npm")
            result.append([*launch, *args[1:]])
            continue
        if exe.suffix.lower() in (".cmd", ".bat", ".ps1") or exe.stem.lower() in ("cmd", "powershell", "pwsh", "bash", "sh", "wscript", "cscript"):
            raise ValueError("Shell wrappers are unsupported for project tests")
        result.append([str(exe), *args[1:]])
    return result

def contained_path(root: Path, candidate: Path) -> Path:
    # Stored artifact paths were canonical when published. Any new indirection,
    # including replacement of the evidence directory by a junction, is unsafe.
    original_root, original_candidate = root.absolute(), candidate.absolute()
    root, candidate = root.resolve(strict=True), candidate.resolve(strict=True)
    if root != original_root or candidate != original_candidate:
        raise ControllerError("artifact_unsafe", "Artifact path changed through a link or junction after publication.")
    if not candidate.is_relative_to(root) or not candidate.is_file() or prohibited_path(candidate.name):
        raise ControllerError("artifact_unsafe", "Artifact no longer resolves inside its run evidence directory.")
    return candidate

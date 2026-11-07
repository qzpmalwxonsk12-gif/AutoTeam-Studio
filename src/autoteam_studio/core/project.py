from __future__ import annotations

import hashlib
from pathlib import Path

from .process import execute
from .safety import check_local_volume, identity, prohibited_path
from .types import ControllerError
from .deadlines import remaining

def git(executable: str, root: Path, *args: str, timeout=10) -> str:
    code, out, err = execute([executable, "-c", "core.hooksPath=NUL" if __import__('os').name == "nt" else "core.hooksPath=/dev/null",
                            "-c", "core.longpaths=true", "-c", "commit.gpgsign=false", "-c", "user.name=AutoTeam Studio", "-c", "user.email=autoteam@localhost",
                            *args], root, timeout, sanitize_output=False)
    if code:
        raise ControllerError("git_failed", f"Git operation failed (exit {code}). {err[-1500:]}")
    return out.strip()

def inspect_project(executable: str, path: Path, require_clean=True):
    root = path.resolve(strict=True)
    check_local_volume(root)
    if not (root / ".git").exists():
        raise ControllerError("git_initialization_required", "Select an existing Git root, or prepare this nonempty folder with Git externally.")
    try:
        actual = Path(git(executable, root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    except ControllerError as exc:
        raise ControllerError("git_initialization_required", "Select an existing Git root, or prepare this nonempty folder with Git externally.") from exc
    if not actual.samefile(root):
        raise ValueError("Select the repository root, not a subdirectory")
    common = Path(git(executable, root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve(strict=True)
    if require_clean and git(executable, root, "status", "--porcelain", "--untracked-files=all"):
        raise ControllerError("project_dirty", "Commit or stash project changes externally before starting. Existing files were preserved.")
    sha = git(executable, root, "rev-parse", "--verify", "HEAD")
    return root, common, identity(common), sha

def prepare(executable: str, path: Path, create: bool) -> Path:
    root = path.resolve()
    check_local_volume(root)
    if not create:
        return inspect_project(executable, root)[0]
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ControllerError("project_not_empty", "Project creation accepts only an absent or explicitly selected empty directory.")
    root.mkdir(parents=True, exist_ok=True)
    git(executable, root, "init", timeout=20)
    git(executable, root, "commit", "--allow-empty", "-m", "AutoTeam empty project baseline", timeout=20)
    return root

def fingerprint(executable: str, root: Path, common: Path) -> dict:
    """Detect original checkout/config mutations; never restore user files."""
    result = {"head": git(executable, root, "rev-parse", "HEAD"),
              "branch": git(executable, root, "branch", "--show-current"),
              "status": git(executable, root, "status", "--porcelain", "--untracked-files=all")}
    listed = git(executable, root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\x00")
    files = {}
    for relative in listed:
        remaining()
        if not relative:
            continue
        if prohibited_path(relative):
            raise ControllerError("credential_filename", "Project contains a prohibited credential filename; no file contents were read.")
        file = root / relative
        if file.is_symlink() or (file.exists() and not file.resolve().is_relative_to(root.resolve())):
            raise ControllerError("project_unsafe", "Tracked links outside the project are unsupported.")
        if file.is_file():
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    remaining()
                    digest.update(chunk)
            files[relative] = digest.hexdigest()
    result["files"] = files
    git_dir = Path(git(executable, root, "rev-parse", "--absolute-git-dir"))
    for label, file in (("config", common / "config"), ("excludes", common / "info/exclude"), ("index", git_dir / "index")):
        result[label] = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
    return result

def scan_commits(executable: str, root: Path, base: str, tip: str):
    commits = git(executable, root, "rev-list", f"{base}..{tip}").splitlines()
    for sha in commits:
        remaining()
        # Inspect filenames in every introduced tree, including merge commits.
        # diff-tree without -m can omit files introduced by a merge entirely.
        # No blob contents (especially credential contents) are read here.
        paths = git(executable, root, "ls-tree", "--full-tree", "--name-only", "-r", "-z", sha).split("\x00")
        if any(prohibited_path(path) for path in paths):
            raise ControllerError("credential_filename", "Introduced history contains a prohibited credential filename.")

"""Project selection and explicit, content-bound local Git snapshots."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from .deadlines import remaining
from .project import git, inspect_project
from .safety import check_local_volume, prohibited_path
from .types import ControllerError


def select_folder(path):
    root = Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ControllerError("folder_required", "Select a project folder.")
    check_local_volume(root)
    if root == Path(root.anchor) or root == Path.home().resolve() or prohibited_path(str(root)):
        raise ControllerError("project_unsafe", "Choose a dedicated project folder, outside account and credential directories.")
    return root


def project_status(executable, path):
    root = select_folder(path)
    if not (root / ".git").exists():
        return {"root": str(root), "ready": False, "can_snapshot": True,
                "detail": "This folder needs its own Git repository. Review and save a baseline to begin."}
    try:
        inspect_project(executable, root)
        return {"root": str(root), "ready": True, "can_snapshot": False, "detail": "Git baseline ready."}
    except ControllerError as exc:
        return {"root": str(root), "ready": False,
                "can_snapshot": exc.code in {"project_dirty", "git_failed"}, "detail": exc.message}


def _files(executable, root):
    # A separate temporary Git directory prevents discovery of a parent's repository.
    # ls-files still respects the selected folder's .gitignore. No user files are written.
    if (root / ".git").exists():
        actual = Path(git(executable, root, "rev-parse", "--show-toplevel")).resolve()
        if actual != root:
            raise ControllerError("project_unsafe", "Select the repository root.")
        git_dir = Path(git(executable, root, "rev-parse", "--absolute-git-dir"))
        if any((git_dir / name).exists() for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD")):
            raise ControllerError("git_in_progress", "Finish the existing merge, rebase or cherry-pick in VS Code first.")
        listing = git(executable, root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        state = git(executable, root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    else:
        with tempfile.TemporaryDirectory(prefix="autoteam-inspect-") as scratch:
            git(executable, Path(scratch), "init")
            listing = git(executable, root, "--git-dir", str(Path(scratch) / ".git"),
                          "--work-tree", str(root), "ls-files", "-z", "--others", "--exclude-standard")
        state = "new repository"
    files = sorted(set(p for p in listing.split("\0") if p))
    hashes = {}
    for relative in files:
        remaining()
        file = root / relative
        if prohibited_path(relative):
            raise ControllerError("credential_filename", "A credential-like filename would enter Git. Exclude it using .gitignore before saving a baseline.")
        if file.is_symlink() or file.is_junction() or file.resolve() != file.absolute():
            raise ControllerError("project_unsafe", "Linked files or directories cannot enter a baseline.")
        if file.is_dir():
            raise ControllerError("project_unsafe", "Nested repositories/submodules require manual preparation in VS Code.")
        if file.exists():
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    remaining()
                    digest.update(chunk)
            hashes[relative] = digest.hexdigest()
        else:
            hashes[relative] = None  # Deletions are shown explicitly in the preview.
    return files, hashes, state


def inspect_baseline(executable, path):
    root = select_folder(path)
    files, hashes, state = _files(executable, root)
    payload = {"root": str(root), "files": hashes, "state": state}
    token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"root": str(root), "files": files, "hashes": hashes, "deleted": [p for p, digest in hashes.items() if digest is None],
            "inspection_id": token, "new_repository": not (root / ".git").exists()}


def save_baseline(executable, path, inspection_id):
    preview = inspect_baseline(executable, path)
    if preview["inspection_id"] != inspection_id:
        raise ControllerError("project_changed", "Files changed after inspection. Review the baseline again.")
    root = Path(preview["root"])
    if preview["new_repository"]:
        git(executable, root, "init")
    # Paths were inspected before staging. No reset, stash, clean or force operation.
    git(executable, root, "add", "-A", "--", ".")
    # Recheck content hashes after staging; tolerate only the expected state change.
    files, hashes, _ = _files(executable, root)
    if {p: h for p, h in hashes.items() if h is not None} != {p: h for p, h in preview["hashes"].items() if h is not None}:
        raise ControllerError("project_changed", "Files changed while staging. Review again; the index and files are preserved.")
    if git(executable, root, "diff", "--name-only", "-z"):
        raise ControllerError("project_changed", "Files changed while staging. Inspect the preserved index in VS Code and retry.")
    git(executable, root, "commit", "--allow-empty", "-m", "AutoTeam user-confirmed project baseline", timeout=30)
    inspect_project(executable, root)
    return root

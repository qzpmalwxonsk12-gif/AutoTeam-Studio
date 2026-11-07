from dataclasses import replace

import pytest

from autoteam_studio.core.api import TeamController, ControllerError
from autoteam_studio.core.project import git


def test_select_non_git_under_parent_repo_and_confirm_baseline(controller, project):
    child = project / "existing folder"
    child.mkdir()
    source = child / "main.js"
    source.write_text("export const answer = () => 42;\n")
    parent_head = git(controller._git(), project, "rev-parse", "HEAD")
    assert controller.select_project(child) == child
    assert not controller.project_status(child)["ready"]
    preview = controller.inspect_baseline(child)
    assert preview["files"] == ["main.js"]
    controller.save_baseline(child, preview["inspection_id"])
    assert controller.project_status(child)["ready"]
    assert git(controller._git(), child, "rev-parse", "--show-toplevel").replace("\\", "/") == child.as_posix()
    assert git(controller._git(), project, "rev-parse", "HEAD") == parent_head
    assert source.read_text() == "export const answer = () => 42;\n"


def test_baseline_preview_changed_files_rejected(controller, tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    file = folder / "hello.txt"
    file.write_text("before")
    preview = controller.inspect_baseline(folder)
    file.write_text("after")
    with pytest.raises(ControllerError, match="changed"):
        controller.save_baseline(folder, preview["inspection_id"])
    assert not (folder / ".git").exists()


def test_baseline_respects_ignores_blocks_credentials_and_preserves_history(controller, project):
    secret = project / "auth.json"
    secret.write_text("dummy test fixture")
    with pytest.raises(ControllerError) as error:
        controller.inspect_baseline(project)
    assert error.value.code == "credential_filename"
    (project / ".gitignore").write_text("auth.json\n")
    (project / "file.txt").write_text("keep")
    before = git(controller._git(), project, "rev-parse", "HEAD")
    preview = controller.inspect_baseline(project)
    assert "auth.json" not in preview["files"]
    controller.save_baseline(project, preview["inspection_id"])
    assert git(controller._git(), project, "rev-parse", "HEAD^") == before
    assert secret.read_text() == "dummy test fixture"
    assert controller.project_status(project)["ready"]


def test_connect_only_checks_saved_login_without_start(controller, tmp_path, monkeypatch):
    homes = [tmp_path / "account-a", tmp_path / "account-b"]
    for home in homes:
        home.mkdir()
        (home / "auth.json").write_text("dummy fixture")
    real = TeamController(replace(controller.settings, backend="real", account_a_home=homes[0], account_b_home=homes[1]))
    calls = []
    monkeypatch.setattr("autoteam_studio.core.api.resolve_launcher", lambda *args: ["mock-codex"])
    monkeypatch.setattr("autoteam_studio.core.codex.verify_login", lambda command, home, folder: calls.append(home))
    result = real.connect_accounts()
    assert calls == homes
    assert all(v["available"] for v in result.values())
    assert not real._workers
    assert all((home / "auth.json").read_text() == "dummy fixture" for home in homes)

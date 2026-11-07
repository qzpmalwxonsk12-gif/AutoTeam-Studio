from dataclasses import replace
from pathlib import Path
import sys
import time

import pytest

from autoteam_studio.contracts import RunSpec
from autoteam_studio.core.api import (ControllerError, ControllerSettings, TeamController, Dependencies,
    PollResponse, validate_response, representative_fixtures, fake_demo_commands)
from autoteam_studio.core.project import git
from autoteam_studio.core.safety import windows_arguments, resolve_tests, sanitized_environment, resolve_launcher
from autoteam_studio.core.storage import read_json
from conftest import wait_terminal

def test_settings_validate_paths_aliases_and_limits(tmp_path):
    with pytest.raises(ValueError): ControllerSettings(test_timeout_seconds=True)
    with pytest.raises(ValueError): ControllerSettings(test_timeout_seconds=7201)
    with pytest.raises(ValueError): ControllerSettings(git_executable=Path("git.exe"))
    with pytest.raises(ValueError): ControllerSettings(account_a_home=tmp_path, account_b_home=tmp_path / "../" / tmp_path.name)
    settings = ControllerSettings(storage_root=tmp_path / "relative/../storage")
    assert settings.storage_root.is_absolute()

def test_fixtures_are_exact_validated_and_independent():
    fixtures = representative_fixtures()
    assert len(fixtures) == 9
    for fixture in fixtures.values():
        validate_response(PollResponse, fixture)
        assert {e.agent for e in fixture["events"]} == {"a", "b"}
    uncertain = fixtures["uncertain_correction_delivery"]
    assert uncertain["corrections"][0].delivered_a
    assert not uncertain["corrections"][0].delivered_b
    assert fixtures["missing_usage"]["snapshot"].reported_tokens_a is None
    bad = fixtures["queued"].copy(); bad["extra"] = 1
    with pytest.raises(ValueError): validate_response(PollResponse, bad)

def test_response_rejects_mixed_generations_relative_worktrees_and_bad_timestamps():
    mixed = representative_fixtures()["running"]
    mixed["details"]["generation"] += 1
    with pytest.raises(ValueError): validate_response(PollResponse, mixed)
    relative = representative_fixtures()["running"]
    relative["details"]["worktrees"]["a"] = "relative/worktree"
    with pytest.raises(ValueError): validate_response(PollResponse, relative)
    invalid = representative_fixtures()["paused"]
    invalid["details"]["controls"]["pause"]["acknowledged_at"] = "2026-13-10T08:13:40.000Z"
    with pytest.raises(ValueError): validate_response(PollResponse, invalid)

def test_fake_detection_does_not_read_credentials(controller, monkeypatch):
    original = Path.open
    def refuse(self, *args, **kwargs):
        assert self.name != "auth.json"
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", refuse)
    result = controller.check_dependencies()
    validate_response(Dependencies, result)
    assert result["account_a"]["available"] and result["worker"]["available"]

def test_prepare_refuses_nonempty_dirty_and_subdirectory(controller, project, tmp_path):
    non_git = tmp_path / "non git"; non_git.mkdir(); (non_git / "valuable.txt").write_text("keep")
    with pytest.raises(ControllerError, match="empty"): controller.prepare_project(non_git, True)
    with pytest.raises(ControllerError) as error: controller.prepare_project(non_git, False)
    assert error.value.code == "git_initialization_required"
    (project / "precious.txt").write_text("preserve")
    with pytest.raises(ControllerError) as error: controller.start(RunSpec(project, "Implement a calculator"))
    assert error.value.code == "project_dirty"
    assert controller.list_history(project) == []
    assert (project / "precious.txt").read_text() == "preserve"

def test_authorization_binds_policy_and_canonical_repo(controller, project, tmp_path):
    commands = fake_demo_commands()
    inspected = controller.inspect_test_authorization(project, commands)
    assert not inspected["authorized"]
    assert "subsequently modify" in inspected["notice"]
    with pytest.raises(ValueError): controller.grant_test_authorization(project, commands, "stale")
    assert controller.grant_test_authorization(project, commands, inspected["inspection_id"])["authorized"]
    different = TeamController(replace(controller.settings, test_timeout_seconds=5))
    assert not different.inspect_test_authorization(project, commands)["authorized"]
    other = controller.prepare_project(tmp_path / "other", True)
    assert not controller.inspect_test_authorization(other, commands)["authorized"]
    with pytest.raises(ValueError): controller.grant_test_authorization(project, (), controller.inspect_test_authorization(project, ())["inspection_id"])

def test_argument_arrays_and_environment(monkeypatch):
    assert windows_arguments('"C:\\Program Files\\Python\\python.exe" -m unittest "some thing"') == ["C:\\Program Files\\Python\\python.exe", "-m", "unittest", "some thing"]
    assert windows_arguments('python "" "say \\"hello\\""') == ["python", "", 'say "hello"']
    for command in ("python & whoami", "python | cmd", "cmd /c dir", 'python "broken'):
        with pytest.raises(ValueError): resolve_tests((command,))
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-retain")
    monkeypatch.setenv("CODEX_HOME", "old-home")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "5")
    env = sanitized_environment(Path("chosen"))
    assert "OPENAI_API_KEY" not in env and "GIT_CONFIG_COUNT" not in env
    assert env["CODEX_HOME"] == "chosen"

def test_shims_resolve_without_executing_batch(tmp_path):
    shim = tmp_path / "codex.cmd"; shim.write_text("do not execute")
    node = tmp_path / "node.exe"; node.write_bytes(b"fixture")
    entry = tmp_path / "node_modules/@openai/codex/bin/codex.js"; entry.parent.mkdir(parents=True); entry.write_text("fixture")
    assert resolve_launcher(shim, "codex") == [str(node.resolve()), str(entry.resolve())]
    arbitrary = tmp_path / "arbitrary.cmd"; arbitrary.write_text("fixture")
    with pytest.raises(ControllerError): resolve_launcher(arbitrary, "worker")

def test_real_start_rejects_missing_cli_without_allocating(controller, project, monkeypatch):
    monkeypatch.setattr("autoteam_studio.core.codex.resolve_launcher", lambda *args: None)
    real = TeamController(replace(controller.settings, backend="real"))
    with pytest.raises(ControllerError) as error: real.start(RunSpec(project, "Implement a calculator"))
    assert error.value.code == "codex_unavailable" and error.value.run_id is None
    assert real.list_history(project) == []

def test_empty_commands_never_complete(controller, project):
    run = controller.start(RunSpec(project, "Implement a simple calculator"))
    result = wait_terminal(controller, run)
    assert result["snapshot"].status == "failed"
    assert result["details"]["reason_code"] == "tests_not_configured"
    assert "Unverified — no project tests configured." == result["snapshot"].error
    assert result["artifacts"]

def test_fake_worker_integrates_and_preserves_original(controller, project, demo_commands):
    executable = controller._git()
    original_sha = git(executable, project, "rev-parse", "HEAD")
    original_branch = git(executable, project, "branch", "--show-current")
    run = controller.start(RunSpec(project, "Implement a tested calculator", test_commands=demo_commands))
    result = wait_terminal(controller, run)
    assert result["snapshot"].status == "completed", result
    sha = result["details"]["result_sha"]
    assert all(g["sha"] == sha for g in result["details"]["gates"] if g["name"] in ("project_tests", "final_review_a", "final_review_b"))
    assert git(executable, project, "rev-parse", "HEAD") == original_sha
    assert git(executable, project, "branch", "--show-current") == original_branch
    assert not git(executable, project, "status", "--porcelain")
    for who in ("a", "b"):
        assert (result["snapshot"].result_dir / f"autoteam_demo/{who}.py").is_file()
    cursor, events = 0, []
    while True:
        batch = controller.poll(run, cursor)
        events.extend(batch["events"]); cursor = batch["next_offset"]
        if not batch["has_more"]: break
    assert {e.agent for e in events} == {"a", "b", "system"}
    assert result["details"]["usage"]["known_calls"] == result["details"]["usage"]["total_calls"]
    assert result["snapshot"].reported_tokens_a > 0
    new_controller = TeamController(replace(controller.settings, storage_root=project.parent / "changed storage"))
    assert new_controller.snapshot(run).status == "completed"
    assert new_controller.list_history(project)[0].run_id == run
    with pytest.raises(ControllerError) as error: controller.stop_at_checkpoint(run)
    assert error.value.code == "run_terminal"

def test_launch_failure_has_failed_history(controller, project, tmp_path):
    failing = TeamController(replace(controller.settings, worker_executable=Path(sys.executable)))
    with pytest.raises(ControllerError) as error:
        failing.start(RunSpec(project, "Implement a useful calculator"))
    assert error.value.run_id
    assert failing.snapshot(error.value.run_id).status == "failed"
    assert failing.list_history(project)[0].run_id == error.value.run_id

def test_unknown_run_and_revision_requires_explicit_source(controller, project):
    with pytest.raises(ControllerError) as error: controller.snapshot("unknown")
    assert error.value.code == "run_not_found"
    with pytest.raises(ValueError): controller.start(RunSpec(project, "Change the calculator", revision=True))
    with pytest.raises(ControllerError): controller.start_revision(RunSpec(project, "Change the calculator"), "unknown")
    for source in (None, "", "   ", 17):
        with pytest.raises(ValueError):
            controller.start_revision(RunSpec(project, "Change the calculator"), source)
    with pytest.raises(ValueError): controller.start(None)
    assert controller.list_history(project) == []

def test_vscode_safe_array_and_artifact_revalidation(controller, project, tmp_path, monkeypatch):
    import subprocess
    captured = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: captured.append((args, kwargs)))
    configured = TeamController(replace(controller.settings, vscode_executable=Path(sys.executable)))
    configured.open_in_vscode(project)
    assert captured[0][0] == [str(Path(sys.executable).resolve()), str(project)]
    assert captured[0][1]["shell"] is False

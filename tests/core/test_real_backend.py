"""Real-mode orchestration with mocked Codex processes; zero model calls."""
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import threading

import pytest

from autoteam_studio.contracts import RunSpec
from autoteam_studio.core.api import TeamController
from autoteam_studio.core.codex import invocation, resolve_accounts, verify_login
from autoteam_studio.core.project import git
from autoteam_studio.core.types import ControllerError
from autoteam_studio.worker.engine import Worker
from conftest import wait_terminal


@pytest.fixture
def real(controller, tmp_path):
    homes = [tmp_path / name for name in ("account A", "account B")]
    for home in homes:
        home.mkdir()
        # Filename presence only: no credential content, even in the fixture.
        (home / "auth.json").touch()
    return TeamController(replace(controller.settings, backend="real", codex_executable=Path(sys.executable),
                                  account_a_home=homes[0], account_b_home=homes[1]))


def mock_worker_launch(monkeypatch):
    class Launch:
        def __init__(self, args, folder, env):
            self.process = self
            self.stdin = io.StringIO()
            worker = Worker(Path(args[args.index("--run-dir") + 1]), args[args.index("--token") + 1], 1)
            self.thread = threading.Thread(target=worker.execute)
            self.thread.start()

        def poll(self):
            return None if self.thread.is_alive() else 0

        def close(self):
            self.thread.join(timeout=120)
    monkeypatch.setattr("autoteam_studio.core.api.OwnedProcess", Launch)


def test_real_workflow_commands_isolation_and_handoffs(real, project, monkeypatch):
    import autoteam_studio.worker.engine as engine
    actual_execute = engine.execute
    calls = []
    rejected = set()
    initial = git(real._git(), project, "rev-parse", "HEAD")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("CODEX_HOME", "must-not-inherit")

    def execute(args, folder, timeout, **kwargs):
        if "exec" not in args and "login" not in args:
            return actual_execute(args, folder, timeout, **kwargs)
        assert args[0] == str(Path(sys.executable).resolve())
        assert 'forced_login_method="chatgpt"' in args
        assert 'approval_policy="never"' in args
        assert 'model_provider="openai"' in args
        assert 'sandbox_workspace_write.writable_roots=[]' in args
        env = kwargs["env"]
        assert "OPENAI_API_KEY" not in env
        who = "a" if env["CODEX_HOME"] == str(real.settings.account_a_home) else "b"
        assert env["CODEX_HOME"] == str(getattr(real.settings, f"account_{who}_home"))
        if "login" in args:
            calls.append((who, "login", ""))
            return 0, "", "Logged in using ChatGPT"
        prompt = kwargs["prompt"]
        stage = prompt.split("; stage ", 1)[1].split(".", 1)[0]
        calls.append((who, stage, prompt))
        writable = stage in ("implementation", "repair", "integrated_repair")
        assert args[args.index("--sandbox") + 1] == ("workspace-write" if writable else "read-only")
        assert args[args.index("-C") + 1] == str(folder)
        assert args[-1] == "-" and "fake_cli.py" not in " ".join(args)
        if stage == "critique":
            assert "proposal_a" in prompt and "proposal_b" in prompt
        if stage == "implementation":
            assert "final_plan_a" in prompt and "challenge_b" in prompt
            (folder / f"component_{who}.py").write_text("def answer():\n    return 42\n")
            tests = folder / "tests"; tests.mkdir(exist_ok=True)
            (tests / f"test_component_{who}.py").write_text(
                f"import unittest\nfrom component_{who} import answer\nclass TestAnswer(unittest.TestCase):\n    def test_answer(self):\n        self.assertEqual(answer(), 42)\n")
        if stage == "repair":
            assert "cross_review_b" in prompt and "Fix the missing helper" in prompt
            with (folder / f"component_{who}.py").open("a") as stream:
                stream.write("\ndef helper():\n    return answer()\n")
        message = f"{stage} response from {who}"
        if stage in {"proposal", "final_plan"}:
            message = json.dumps({"decision": "implement", "message": message})
        if stage.endswith("_review"):
            schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
            assert schema["additionalProperties"] is False
            scope = "cross" if stage == "cross_review" else "final"
            reject = scope == "cross" and who == "b" and who not in rejected
            if reject:
                rejected.add(who)
            findings = [{"id": "helper", "severity": "medium", "path": "component_a.py", "line": 1,
                         "message": "Missing helper", "required_change": "Fix the missing helper"}] if reject else []
            message = json.dumps({"schema_version": 1, "reviewer": who, "scope": scope,
                "reviewed_sha": git(real._git(), folder, "rev-parse", "HEAD"), "base_sha": initial,
                "passed": not reject, "summary": "Mocked diff review", "blocking_findings": findings,
                "nonblocking_findings": [], "evidence_artifact_ids": []})
        kwargs["on_submitted"]()
        for record in ({"type": "item.completed", "item": {"type": "agent_message", "text": message}},
                       {"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 3}}):
            kwargs["on_line"]("stdout", json.dumps(record))
        return 0, "", ""

    monkeypatch.setattr(engine, "execute", execute)
    monkeypatch.setattr("autoteam_studio.core.codex.execute", execute)
    mock_worker_launch(monkeypatch)
    commands = (f'"{sys.executable}" -m unittest discover -s tests',)
    inspected = real.inspect_test_authorization(project, commands)
    real.grant_test_authorization(project, commands, inspected["inspection_id"])
    run = real.start(RunSpec(project, "Implement two tested components", test_commands=commands))
    wait_terminal(real, run, timeout=120)
    assert real.snapshot(run).status == "completed", real.snapshot(run)
    assert [(who, stage) for who, stage, _ in calls[:2]] == [("a", "login"), ("b", "login")]
    assert [(who, stage) for who, stage, _ in calls if stage in ("plan", "challenge", "final_plan")] == [
        ("a", "plan"), ("b", "challenge"), ("a", "final_plan")]
    assert {stage for _, stage, _ in calls} >= {"implementation", "repair", "cross_review", "final_review"}
    assert git(real._git(), project, "rev-parse", "HEAD") == initial
    assert git(real._git(), project, "status", "--porcelain") == ""
    result = real.snapshot(run).result_dir
    assert (result / "component_a.py").is_file() and (result / "component_b.py").is_file()
    assert not (result / "autoteam_demo").exists()
    details = real.run_details(run)
    assert details["backend"] == "real"
    assert details["usage"]["known_calls"] == details["usage"]["total_calls"]
    assert all(g["sha"] == details["result_sha"] for g in details["gates"] if g["name"] in ("project_tests", "final_review_a", "final_review_b"))


@pytest.mark.parametrize("code, message", [(0, "Logged in using an API key"), (1, "Not logged in")])
def test_login_policy_rejects_api_or_missing_login(monkeypatch, tmp_path, code, message):
    monkeypatch.setattr("autoteam_studio.core.codex.execute", lambda *a, **k: (code, message, ""))
    with pytest.raises(ControllerError, match="saved ChatGPT login"):
        verify_login(["mock-codex"], tmp_path, tmp_path)


def test_auth_failure_retains_real_run_history(real, project, monkeypatch):
    monkeypatch.setattr("autoteam_studio.core.codex.execute", lambda *a, **k: (1, "", "Not logged in"))
    mock_worker_launch(monkeypatch)
    run = real.start(RunSpec(project, "Implement two tested components"))
    wait_terminal(real, run)
    assert real.snapshot(run).status == "failed"
    assert real.run_details(run)["reason_code"] == "chatgpt_login_required"
    assert real.list_history(project)[0].run_id == run
    assert all(value is None for value in real.run_details(run)["worktrees"].values())


def test_accounts_must_stay_outside_selected_workspace(real, project):
    home = project / "account"; home.mkdir(); (home / "auth.json").touch()
    settings = replace(real.settings, account_a_home=home)
    with pytest.raises(ControllerError) as error:
        resolve_accounts(settings, project)
    assert error.value.code == "account_home_unsafe"


def test_native_windows_policy_uses_no_elevation_or_unrestricted_fallback(monkeypatch):
    monkeypatch.setattr("autoteam_studio.core.codex.os.name", "nt")
    args = invocation(["codex.exe"], "worktree with spaces", True)
    assert 'windows.sandbox="unelevated"' in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert "danger-full-access" not in args


@pytest.mark.parametrize("stop_stage,decision,expected_calls", [("proposal", "no_work", 2), ("final_plan", "needs_input", 7)])
def test_no_task_stops_pipeline_before_implementation(real, project, monkeypatch, stop_stage, decision, expected_calls):
    import autoteam_studio.worker.engine as engine
    calls = []
    def execute(args, folder, timeout, **kwargs):
        stage = kwargs["prompt"].split("; stage ", 1)[1].split(".", 1)[0]
        calls.append(stage)
        message = json.dumps({"decision": decision if stage == stop_stage else "implement",
                              "message": "No software task was requested."}) if stage in {"proposal", "final_plan"} else "Reviewing the task."
        kwargs["on_submitted"]()
        kwargs["on_line"]("stdout", json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": message}}))
        return 0, "", ""
    monkeypatch.setattr(engine, "execute", execute)
    monkeypatch.setattr(engine, "verify_login", lambda *args: None)
    mock_worker_launch(monkeypatch)
    run = real.start(RunSpec(project, "hello what are you both doing?"))
    result = wait_terminal(real, run, timeout=60)
    assert result["snapshot"].status == "stopped"
    assert result["snapshot"].stage == "awaiting_task"
    assert len(calls) == expected_calls and "implementation" not in calls
    assert result["details"]["reason_code"] == "task_needs_input"
    assert not list(Path(result["details"]["worktrees"]["a"]).glob("*.py"))

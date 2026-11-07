import json
import threading
import time
from pathlib import Path

import pytest

from autoteam_studio.core.chat import ChatService, chat_prompt, context_messages
from autoteam_studio.core.codex import chat_invocation
from autoteam_studio.core.types import ControllerSettings, ControllerError
from autoteam_studio.worker.intent import NoImplementation, read_decision


@pytest.fixture
def chat(tmp_path):
    return ChatService(ControllerSettings(backend="fake"), tmp_path)


def test_greeting_is_two_replies_with_no_project_or_build(chat):
    record = chat.open()["conversation"]
    result = chat.send(record["id"], "hello what are you both doing?")
    assert [m["speaker"] for m in result["messages"]] == ["you", "a", "b"]
    assert all(m["state"] == "delivered" for m in result["messages"])
    assert not result["build_prompt"]
    assert not list(chat.root.rglob(".git"))
    assert not list(chat.root.rglob("manifest.json"))
    assert chat.open()["conversation"]["messages"] == result["messages"]


def test_shared_history_and_casual_mode_never_offer_build(chat):
    record = chat.open()["conversation"]
    first = chat.send(record["id"], "hello")
    context = context_messages(first["messages"])
    assert [m["speaker"] for m in context] == ["you", "a", "b"]
    assert json.dumps(context, ensure_ascii=False) in chat_prompt("a", context, False)
    assert json.dumps(context, ensure_ascii=False) in chat_prompt("b", context, False)
    result = chat.send(record["id"], "Build a calculator", casual=True)
    assert not result["build_prompt"]
    assert len(result["messages"]) == 6
    result = chat.send(record["id"], "Build a calculator", casual=False)
    assert result["build_prompt"] == "Build a calculator"
    assert not list(chat.root.rglob("manifest.json"))


def test_chat_partial_failure_keeps_history_and_does_not_offer_build(chat):
    identity = chat.open()["conversation"]["id"]
    result = chat.send(identity, "Build a calculator [fake:fail-b]")
    assert [m["state"] for m in result["messages"][-2:]] == ["delivered", "failed"]
    assert not result["build_prompt"]
    assert chat.read(identity) == result
    other = chat.open(new=True)["conversation"]
    assert other["id"] != identity
    assert chat.read(identity) == result


def test_cancel_chat_reaps_simulated_workers(chat):
    identity = chat.open()["conversation"]["id"]
    result = []
    thread = threading.Thread(target=lambda: result.append(chat.send(identity, "hello [fake:slow]")))
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(m["state"] == "awaiting_reply" for m in chat.read(identity)["messages"]):
            break
        time.sleep(.02)
    chat.cancel()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert all(m["state"] == "cancelled" for m in result[0]["messages"][-2:])


def test_chat_policy_cannot_target_project_or_inherit_tool_configuration(tmp_path):
    args = chat_invocation(["codex.exe"], tmp_path, tmp_path / "schema.json")
    assert "--ignore-user-config" in args and "--skip-git-repo-check" in args
    assert 'features.shell_tool=false' in args and 'features.apps=false' in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "workspace-write" not in args


@pytest.mark.parametrize("decision", ["no_work", "needs_input"])
def test_pipeline_declines_non_tasks(decision):
    with pytest.raises(NoImplementation):
        read_decision([("message", json.dumps({"decision": decision, "message": "No development task was requested."}), {})])


def test_pipeline_rejects_unstructured_or_empty_decision():
    for text in ('Hello!', '{}', '[]', '{"decision":"implement","message":""}'):
        with pytest.raises(ControllerError):
            read_decision([("message", text, {})])


def test_real_chat_uses_two_distinct_homes_and_shared_history_without_project(monkeypatch, tmp_path):
    import autoteam_studio.core.chat as module
    from dataclasses import replace
    settings = ControllerSettings(backend="real")
    service = ChatService(settings, tmp_path)
    homes = {"a": tmp_path / "home-a", "b": tmp_path / "home-b"}
    monkeypatch.setattr(module, "resolve_accounts", lambda settings, folder: (["mock-codex"], homes))
    calls = []
    def execute(args, folder, timeout, **kwargs):
        calls.append((args, folder, kwargs["prompt"], kwargs["env"]["CODEX_HOME"]))
        assert args[0] == "mock-codex"
        assert "--ignore-user-config" in args and "--ephemeral" in args
        assert args[args.index("--sandbox") + 1] == "read-only"
        assert folder.is_relative_to(service.root)
        kwargs["on_submitted"]()
        response = json.dumps({"message": "Hello!", "build_prompt": ""})
        kwargs["on_line"]("stdout", json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": response}}))
        return 0, "", ""
    monkeypatch.setattr(module, "execute", execute)
    identity = service.open()["conversation"]["id"]
    service.send(identity, "hello")
    service.send(identity, "Do you remember what we said?")
    assert len(calls) == 4
    assert {c[3] for c in calls} == {str(v) for v in homes.values()}
    for _, _, prompt, _ in calls[-2:]:
        assert '"speaker": "a"' in prompt and '"speaker": "b"' in prompt


def test_work_summary_persists_once_and_enters_chat_context(chat):
    identity = chat.open()["conversation"]["id"]
    chat.record_run(identity, "run-1", "Calculator completed with passing tests.", "completed")
    result = chat.record_run(identity, "run-1", "Calculator completed with passing tests.", "completed")
    assert len(result["messages"]) == 1
    assert "Calculator completed" in chat_prompt("a", context_messages(result["messages"]), False)

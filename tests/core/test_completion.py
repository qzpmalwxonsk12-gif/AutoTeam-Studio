import json
import sys
import time
from pathlib import Path

import pytest

from autoteam_studio.contracts import RunSpec
from autoteam_studio.core.api import ControllerError
from autoteam_studio.core.process import execute, alive
from autoteam_studio.core.project import git
from autoteam_studio.core.events import normalize
from autoteam_studio.core.storage import event_payload
from autoteam_studio.worker.gates import substantive, verify_disjoint, verify_repair_paths, code_signature
from conftest import wait_terminal
from test_controls_recovery import wait_for


@pytest.mark.parametrize("source,test,body,test_body", [
    ("src/add.js", "test/add.test.js", "export function add(a,b) { return a+b; }", "test('add', () => assert.equal(add(1,2),3));"),
    ("src/lib.rs", "tests/add.rs", "pub fn add(a:i32,b:i32)->i32 { a+b }", "#[test] fn addition() { assert_eq!(add(1,2),3); }"),
    ("src/Add.java", "test/AddTest.java", "class Add { int add(int a,int b) { return a+b; } }", "class AddTest { @Test void adds() { assertEquals(3, add(1,2)); } }"),
    ("add.go", "add_test.go", "package calc\nfunc Add(a,b int) int { return a+b }", "package calc\nfunc TestAdd(t *testing.T) { if Add(1,2)!=3 { t.Fatal(\"bad\") } }"),
])
def test_multiple_language_contributions(controller, project, source, test, body, test_body):
    base = git(controller._git(), project, "rev-parse", "HEAD")
    for name, content in ((source, body), (test, test_body)):
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(controller._git(), project, "add", ".")
    git(controller._git(), project, "commit", "-m", "fixture")
    sha = git(controller._git(), project, "rev-parse", "HEAD")
    evidence = substantive(controller._git(), project, base, sha, sha)
    assert evidence["source_paths"] == [source]
    assert evidence["test_paths"] == [test]
    assert len(evidence["owner_blobs"]) == 2


def test_comment_formatting_and_cross_owner_changes_rejected(controller, project):
    assert code_signature("// comment\n", ".js") == ""
    assert code_signature("const a = '// string';", ".js") == code_signature("const a='// string'; /*note*/", ".js")
    with pytest.raises(ControllerError, match="same paths"):
        verify_disjoint({"a": ["shared.ts"], "b": ["shared.ts"]})
    (project / "other-owner.js").write_text("const answer = 42;")
    with pytest.raises(ControllerError, match="outside"):
        verify_repair_paths(controller._git(), project, ["my-file.js"])


def test_timeout_retains_sanitized_partial_diagnostics(tmp_path):
    with pytest.raises(ControllerError) as error:
        execute([sys.executable, "-u", "-c", "import time; print('working'); print('api_key=secret'); time.sleep(9)"], tmp_path, 1)
    assert error.value.code == "process_timeout"
    assert "working" in error.value.stdout
    assert "secret" not in error.value.stdout


def test_event_command_output_and_file_actions_survive_publication():
    record = {"type": "item.completed", "item": {"type": "command_execution", "command": "node --test", "exit_code": 1, "status": "failed", "aggregated_output": "1 test failed\naccess_token=secret"}}
    kind, message, payload = normalize(json.dumps(record), "call", "tests")[0]
    saved = event_payload(payload)
    assert kind == "command" and message == "node --test"
    assert "1 test failed" in saved["output"] and "secret" not in saved["output"]
    record["item"] = {"type": "file_change", "changes": [{"path": "main.ts", "kind": "update"}]}
    assert normalize(json.dumps(record), "call", "implementation")[0][1] == "update: main.ts"


def test_stopped_run_resumes_without_repeating_completed_calls(controller, project, demo_commands):
    run = controller.start(RunSpec(project, "Build a tested utility", test_commands=demo_commands))
    wait_for(controller, run, lambda r: r["details"]["usage"]["total_calls"] >= 4)
    controller.pause_next_tasks(run)
    wait_for(controller, run, lambda r: r["snapshot"].status == "paused")
    controller.stop_at_checkpoint(run)
    stopped = wait_terminal(controller, run)
    assert stopped["snapshot"].status == "stopped"
    store, _ = controller._lookup(run)
    deadline = time.monotonic() + 5
    while alive(store.read().get("worker")) and time.monotonic() < deadline:
        time.sleep(.05)
    assert controller.resume_stopped(run) == run
    done = wait_terminal(controller, run, timeout=80)
    assert done["snapshot"].status == "completed", done["snapshot"]
    assert done["details"]["usage"]["total_calls"] == 16
    assert len(controller.list_history(project)) == 1
    assert len([a for a in done["artifacts"] if a["kind"] == "report"]) == 2


def test_resume_rejects_modified_checkpoint(controller, project, demo_commands):
    run = controller.start(RunSpec(project, "Build another tested utility", test_commands=demo_commands))
    wait_for(controller, run, lambda r: r["details"]["usage"]["total_calls"] >= 2)
    controller.stop_at_checkpoint(run)
    stopped = wait_terminal(controller, run)
    store, _ = controller._lookup(run)
    deadline = time.monotonic() + 5
    while alive(store.read().get("worker")) and time.monotonic() < deadline:
        time.sleep(.05)
    changed = Path(stopped["details"]["worktrees"]["a"]) / "user-edit.txt"
    changed.write_text("must survive")
    with pytest.raises(ControllerError) as error:
        controller.resume_stopped(run)
    assert error.value.code == "checkpoint_changed"
    assert changed.read_text() == "must survive"

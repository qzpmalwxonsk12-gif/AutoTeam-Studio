import json
import os
from pathlib import Path
import sys
import time

import pytest

from autoteam_studio.core.api import ControllerError
from autoteam_studio.core.process import execute, alive, own_identity
from autoteam_studio.core.project import git, scan_commits
from autoteam_studio.worker.gates import validate_review, substantive

def test_pipes_drain_concurrently_and_prompt_submission(tmp_path):
    acknowledgments = []
    script = "import sys; p=sys.stdin.read(); sys.stderr.write('e'*200000); sys.stderr.flush(); print('received '+str(len(p)))"
    code, out, err = execute([sys.executable, "-c", script], tmp_path, 5, prompt="p" * 200000,
                             on_submitted=lambda: acknowledgments.append(True))
    assert code == 0 and "received 200000" in out and len(err) == 200000
    assert acknowledgments == [True]

def test_timeout_kills_descendants(tmp_path):
    pidfile = tmp_path / "descendant.json"
    script = tmp_path / "spawn.py"
    script.write_text("import subprocess, sys, time, json\nfrom pathlib import Path\n"
        "p=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "Path('descendant.json').write_text(json.dumps({'pid':p.pid}))\ntime.sleep(60)\n")
    started = time.monotonic()
    with pytest.raises(ControllerError) as error: execute([sys.executable, str(script)], tmp_path, 1)
    assert error.value.code == "process_timeout"
    assert time.monotonic() - started < 5
    pid = json.loads(pidfile.read_text())["pid"]
    from autoteam_studio.core.process import process_identity
    assert process_identity(pid) is None

def test_output_limit_fails_instead_of_truncating_success(tmp_path):
    with pytest.raises(ControllerError) as error:
        execute([sys.executable, "-c", "print('x'*2000000)"], tmp_path, 5)
    assert error.value.code == "output_limit"

def review():
    return {"schema_version": 1, "reviewer": "a", "scope": "final", "reviewed_sha": "1" * 40,
            "base_sha": "0" * 40, "passed": True, "summary": "Reviewed diff", "blocking_findings": [],
            "nonblocking_findings": [], "evidence_artifact_ids": []}

def test_reviews_fail_wrong_sha_malformed_and_inconsistent_approval():
    result = review()
    args = dict(reviewer="a", scope="final", reviewed_sha="1" * 40, base_sha="0" * 40)
    assert validate_review(result, **args)
    with pytest.raises(ControllerError): validate_review({**result, "reviewed_sha": "2" * 40}, **args)
    with pytest.raises(ControllerError): validate_review({**result, "extra": 1}, **args)
    with pytest.raises(ControllerError): validate_review({**result, "schema_version": True}, **args)
    finding = {"id": "finding", "severity": "high", "path": "source.py", "line": 1,
               "message": "Issue", "required_change": "Fix issue"}
    assert not validate_review({**result, "nonblocking_findings": [finding]}, **args)
    assert not validate_review({**result, "passed": False}, **args)

def test_contribution_rejects_formatting_only_and_discarded_changes(controller, project):
    exe = controller._git()
    base = git(exe, project, "rev-parse", "HEAD")
    (project / "module.py").write_text("# only a comment\n")
    git(exe, project, "add", "module.py"); git(exe, project, "commit", "-m", "Comment")
    tip = git(exe, project, "rev-parse", "HEAD")
    with pytest.raises(ControllerError): substantive(exe, project, base, tip)
    (project / "module.py").write_text("def answer():\n    return 42\n")
    tests = project / "tests"; tests.mkdir()
    (tests / "test_module.py").write_text("from module import answer\ndef test_answer():\n    assert answer() == 42\n")
    git(exe, project, "add", "."); git(exe, project, "commit", "-m", "Source and test")
    source_tip = git(exe, project, "rev-parse", "HEAD")
    assert substantive(exe, project, base, source_tip)["test_paths"] == ["tests/test_module.py"]
    (project / "module.py").write_text("# discarded\n")
    git(exe, project, "add", "."); git(exe, project, "commit", "-m", "Discard")
    with pytest.raises(ControllerError): substantive(exe, project, base, source_tip, git(exe, project, "rev-parse", "HEAD"))

def test_intermediate_credential_filename_commit_is_rejected(controller, project):
    exe = controller._git()
    base = git(exe, project, "rev-parse", "HEAD")
    # A dummy filename fixture, never any account credential content.
    dummy = project / "auth.json"; dummy.write_text("{}")
    git(exe, project, "add", "auth.json"); git(exe, project, "commit", "-m", "Prohibited fixture filename")
    dummy.unlink(); git(exe, project, "add", "-u"); git(exe, project, "commit", "-m", "Remove fixture")
    with pytest.raises(ControllerError) as error:
        scan_commits(exe, project, base, git(exe, project, "rev-parse", "HEAD"))
    assert error.value.code == "credential_filename"

@pytest.mark.parametrize("protocol, token", [(2, "expected"), (1, "intruder")])
def test_invalid_handoff_cannot_modify_run_or_reservation(tmp_path, protocol, token):
    from autoteam_studio.core.api import initial_state
    from autoteam_studio.core.storage import Store, atomic, read_json
    from autoteam_studio.worker.engine import Worker
    root = tmp_path / "run"
    root.mkdir()
    store = Store(root)
    store.publish(initial_state("run", tmp_path, "identity", "fake"))
    manifest = {"schema_version": 1, "token": "expected", "project_root": str(tmp_path),
                "git": "unused", "base_sha": "0" * 40}
    atomic(root / "manifest.json", manifest)
    before = store.read()
    with pytest.raises(ControllerError) as error:
        Worker(root, token, protocol).execute()
    assert error.value.code in ("protocol_mismatch", "handoff_invalid")
    assert store.read() == before
    assert read_json(root / "manifest.json") == manifest
    assert not (root / "events.jsonl").exists()

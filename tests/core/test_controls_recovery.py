from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sys
import time

import pytest

from autoteam_studio.contracts import RunSpec
from autoteam_studio.core.api import TeamController, ControllerError
from autoteam_studio.core.storage import Store, guard, atomic, read_json
from autoteam_studio.core.project import git
from conftest import wait_terminal

def wait_for(controller, run, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = controller.poll(run, 0)
        if predicate(response): return response
        if response["snapshot"].status in ("completed", "failed", "stopped"):
            raise AssertionError(response)
        time.sleep(.05)
    raise AssertionError("Checkpoint did not acknowledge requested action")

def test_ordered_corrections_pause_resume_sticky_stop_and_reservation(controller, project):
    run = controller.start(RunSpec(project, "Implement a tested utility"))
    controller.pause_next_tasks(run)
    paused = wait_for(controller, run, lambda r: r["snapshot"].status == "paused")
    assert paused["details"]["controls"]["pause"]["acknowledged"]
    count = paused["details"]["usage"]["total_calls"]
    with pytest.raises(ControllerError) as error: controller.start(RunSpec(project, "Implement another utility"))
    assert error.value.code == "project_reserved"
    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(lambda i: controller.queue_correction(run, f"Correction {i}: preserve original checkout"), range(3)))
    waiting = controller.run_details(run)
    sequences = [c["sequence"] for c in waiting["controls"]["corrections"]]
    assert len(set(sequences)) == 3 and sequences == sorted(sequences)
    time.sleep(.2)
    assert controller.run_details(run)["usage"]["total_calls"] == count
    controller.resume(run)
    delivered = wait_for(controller, run, lambda r: all(c.delivered_a and c.delivered_b for c in r["corrections"]))
    assert {c.id for c in delivered["corrections"]} == set(ids)
    controller.stop_at_checkpoint(run)
    controller.resume(run)
    terminal = wait_terminal(controller, run)
    assert terminal["snapshot"].status == "stopped"
    assert terminal["details"]["controls"]["stop"]["acknowledged"]
    assert terminal["details"]["controls"]["last_processed_sequence"] == terminal["details"]["controls"]["last_published_sequence"]
    with pytest.raises(ControllerError) as error: controller.queue_correction(run, "Too late")
    assert error.value.code == "run_terminal"

def test_worker_crash_preserves_worktrees_history_and_recovers(controller, project):
    run = controller.start(RunSpec(project, "Implement a tested utility [fake:crash]"))
    result = wait_terminal(controller, run)
    assert result["snapshot"].status == "failed" and result["details"]["reason_code"] == "worker_died"
    assert all(Path(p).is_dir() for p in result["details"]["worktrees"].values())
    assert any(a["kind"] == "text" for a in result["artifacts"])
    reopened = TeamController(replace(controller.settings, storage_root=project.parent / "alternate"))
    assert reopened.list_history(project)[0].status == "failed"

def test_dead_worker_submitting_correction_becomes_uncertain(controller, project):
    run = controller.start(RunSpec(project, "Implement a tested utility"))
    controller.pause_next_tasks(run)
    wait_for(controller, run, lambda r: r["snapshot"].status == "paused")
    cid = controller.queue_correction(run, "Preserve the public API")
    store, _ = controller._lookup(run)
    def fixture(state):
        state["details"]["controls"]["corrections"][0]["b"].update(state="submitting", call_id="interrupted-call")
    store.transaction(fixture)
    controller._workers[run].close()
    result = controller.poll(run, 0)
    assert result["snapshot"].status == "failed"
    delivery = result["details"]["controls"]["corrections"][0]
    assert delivery["id"] == cid and delivery["b"]["state"] == "delivery_uncertain"
    assert not result["corrections"][0].delivered_b

def test_recovery_detects_original_checkout_changes_without_restoring(controller, project):
    run = controller.start(RunSpec(project, "Implement a tested utility"))
    controller.pause_next_tasks(run)
    wait_for(controller, run, lambda r: r["snapshot"].status == "paused")
    precious = project / "user_work.txt"
    precious.write_text("Preserve this work after a crash", encoding="utf-8")
    controller._workers[run].close()
    result = controller.poll(run, 0)
    assert result["snapshot"].status == "failed"
    assert result["details"]["reason_code"] == "original_checkout_changed"
    assert precious.read_text(encoding="utf-8") == "Preserve this work after a crash"

def test_explicit_revision_uses_source_and_cross_review_repairs(controller, project, demo_commands):
    first = controller.start(RunSpec(project, "Implement a tested calculator [fake:cross-repair-a]", test_commands=demo_commands))
    result = wait_terminal(controller, first, timeout=60)
    assert result["snapshot"].status == "completed", result["snapshot"]
    cross = [g for g in result["details"]["gates"] if g["name"] == "cross_review_b"]
    assert [g["state"] for g in cross] == ["failed", "passed"]
    assert cross[0]["sha"] != cross[1]["sha"]
    second = controller.start_revision(RunSpec(project, "Extend the calculator with new tests", revision=False, test_commands=demo_commands), first)
    revised = wait_terminal(controller, second, timeout=60)
    assert revised["snapshot"].status == "completed", revised["snapshot"]
    assert revised["details"]["base_sha"] == result["details"]["result_sha"]
    assert revised["details"]["source_run_id"] == first

@pytest.mark.parametrize("rounds, expected", [(1, "failed"), (2, "completed")])
def test_integrated_review_requires_another_pass_for_repair(controller, project, demo_commands, rounds, expected):
    run = controller.start(RunSpec(project, "Implement a tested calculator [fake:integrated-repair]",
        test_commands=demo_commands, review_rounds=rounds))
    result = wait_terminal(controller, run, timeout=60)
    assert result["snapshot"].status == expected, result["snapshot"]
    reviews = [g for g in result["details"]["gates"] if g["name"] == "final_review_a"]
    assert reviews[0]["state"] == "failed"
    if rounds == 2:
        assert reviews[-1]["state"] == "passed"
        assert reviews[-1]["sha"] == result["details"]["result_sha"]
        assert reviews[0]["sha"] != reviews[-1]["sha"]
        # Repairs must not credit the combined result to both owners.
        import json
        retained = {}
        for owner in ("a", "b"):
            artifact = next(a for a in reversed(result["artifacts"]) if a["label"].startswith(f"Retained contribution {owner.upper()}"))
            evidence = json.loads(read_json(Path(artifact["path"]))["text"])
            retained[owner] = set(evidence["retained_paths"])
            assert evidence["source_paths"] == [f"autoteam_demo/{owner}.py"]
            assert evidence["test_paths"] == [f"tests/test_autoteam_demo_{owner}.py"]
        assert retained["a"].isdisjoint(retained["b"])
    else:
        assert len(reviews) == 1
        assert result["details"]["reason_code"] == "acceptance_failed"

def test_authorized_command_mutating_source_cannot_publish_completion(controller, project):
    # Deliberately authorize a standard-library fixture that changes a result
    # file and exits zero. An exit code alone must not establish acceptance.
    commands = (f'"{sys.executable}" -c "from pathlib import Path; Path(\'autoteam_demo/a.py\').write_text(\'changed\')"',)
    inspected = controller.inspect_test_authorization(project, commands)
    controller.grant_test_authorization(project, commands, inspected["inspection_id"])
    run = controller.start(RunSpec(project, "Implement a tested calculator", test_commands=commands))
    result = wait_terminal(controller, run, timeout=60)
    assert result["snapshot"].status == "failed"
    assert result["details"]["reason_code"] == "result_mutated"
    assert (Path(result["details"]["worktrees"]["result"]) / "autoteam_demo/a.py").read_text() == "changed"
    assert not git(controller._git(), project, "status", "--porcelain")

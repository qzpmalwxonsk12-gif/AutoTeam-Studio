"""Provisional frozen-schema double until A's validated fixtures are available.

No production fallback uses this double. Integration must replace fixture()
with A's shared fixture provider once its export name is known.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from autoteam_studio.contracts import RunSnapshot, AgentEvent, Correction

STAMP = "2026-10-10T08:13:40.000Z"


def fixture(root, state="running"):
    def checkpoint():
        return dict(requested=False, acknowledged=False, request_sequence=None, acknowledged_at=None)
    snapshot = RunSnapshot("run-1", state, "implementation", root / "result",
                           "Launch failed" if state == "failed" else None)
    details = dict(
        schema_version=1, generation=1, backend="fake", project_root=str(root),
        project_identity="fixture-identity", source_run_id=None, base_sha="abc",
        result_sha=None, result_branch=None,
        worktrees={"a": str(root / "a"), "b": str(root / "b"), "result": str(root / "result")},
        reason_code="launch_failed" if state == "failed" else None,
        gates=[dict(name="tests", state="not_run", sha=None,
                    explanation="Unverified — no project tests configured.", artifact_ids=[])],
        controls=dict(last_published_sequence=0, last_processed_sequence=0,
                      pause=checkpoint(), stop=checkpoint(), corrections=[]),
        active_calls=[dict(account="a", call_id="a-1", stage="implementation", started_at=STAMP,
                           deadline="2026-10-10T08:33:40.000Z", last_activity_at=None)] if state == "running" else [],
        usage=dict(known_calls=0, total_calls=2, cached_input_a=None, cached_input_b=None),
    )
    return dict(schema_version=1, generation=1, snapshot=snapshot, details=details, corrections=[],
                artifacts=[dict(id="report", label="Final report", kind="report", path=str(root / "report.txt"))],
                events=[AgentEvent("run-1", "a", STAMP, "message", "A implements source"),
                        AgentEvent("run-1", "b", STAMP, "file_change", "B writes tests")],
                next_offset=200, has_more=False)


class LaunchFailure(RuntimeError):
    code = "worker_launch_failed"
    run_id = "run-1"


class ControllerDouble:
    def __init__(self, root):
        self.root = root
        self.response = fixture(root)
        self.calls = []
        self.launch_failure = False

    def check_dependencies(self):
        return {key: dict(available=True, detail="fixture available", path=None)
                for key in ("git", "vscode", "codex", "runtime", "account_a", "account_b", "worker")}

    def prepare_project(self, path, create):
        self.calls.append(("prepare", path, create))
        return path

    def start(self, spec):
        self.calls.append(("start", spec))
        if self.launch_failure:
            self.response = fixture(self.root, "failed")
            raise LaunchFailure("Worker could not launch")
        return "run-1"

    def start_revision(self, spec, source_run_id):
        self.calls.append(("revision", spec, source_run_id))
        return "run-2"

    def poll(self, run_id, offset):
        self.calls.append(("poll", run_id, offset))
        result = deepcopy(self.response)
        if offset:
            result["events"] = []
        return result

    def list_history(self, project_root):
        return [self.response["snapshot"]]

    def reconcile(self, project_root=None):
        return [self.response["snapshot"]]

    def queue_correction(self, run_id, text):
        self.calls.append(("correction", run_id, text))
        return "correction-1"

    def pause_next_tasks(self, run_id):
        self.calls.append(("pause", run_id))

    def resume(self, run_id):
        self.calls.append(("resume", run_id))

    def stop_at_checkpoint(self, run_id):
        self.calls.append(("stop", run_id))
        self.response["snapshot"] = replace(self.response["snapshot"], status="stopped")

    def open_in_vscode(self, path):
        self.calls.append(("vscode", path))

    def open_artifact(self, run_id, artifact_id):
        self.calls.append(("artifact", run_id, artifact_id))

    def inspect_test_authorization(self, project_root, commands):
        self.calls.append(("inspect", project_root, commands))
        return dict(schema_version=1, inspection_id="inspection-1", project_identity="fixture-identity",
                    commands=[["C:/Python312/python.exe", "-m", "unittest"]],
                    working_directory="result_worktree", environment_policy="sanitized_inherited_v1",
                    timeout_seconds=600, execution="sequential", authorized=False,
                    authorization_id=None, granted_at=None,
                    notice="Project code which agents may subsequently modify will execute.")

    def grant_test_authorization(self, project_root, commands, inspection_id):
        self.calls.append(("grant", project_root, commands, inspection_id))
        value = self.inspect_test_authorization(project_root, commands)
        value.update(authorized=True, authorization_id="grant-1", granted_at=STAMP)
        return value

"""Validated UI fixtures using exactly the production response shapes.

Fixtures are offline examples, never evidence that a real model ran.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .api import initial_state, _snapshot, _corrections
from .types import PollResponse, validate_response
from autoteam_studio.contracts import AgentEvent

FIXTURE_NAMES = ("queued", "running", "paused", "completed", "failed", "stopped",
                 "uncertain_correction_delivery", "missing_usage", "failed_launch")

def poll_fixture(name: str, root: Path | None = None) -> PollResponse:
    if name not in FIXTURE_NAMES:
        raise ValueError("Unknown fixture")
    root = (root or Path.cwd() / "offline-fixture").resolve()
    state = initial_state("fixture-" + name, root, "fixture-project-identity", "fake")
    state["generation"] = state["details"]["generation"] = 3
    status = name if name in ("queued", "running", "paused", "completed", "failed", "stopped") else "failed" if name == "failed_launch" else "running"
    state["snapshot"].update(status=status, stage="implementation" if status in ("running", "paused") else status)
    stamp = "2026-10-10T08:13:40.000Z"
    if status != "queued" and name != "failed_launch":
        state["details"]["worktrees"] = {who: str(root / "worktrees" / who) for who in ("a", "b", "result")}
    if status == "completed":
        state["snapshot"]["result_dir"] = str(root / "worktrees/result")
        state["details"]["result_sha"] = "a" * 40
        state["details"]["gates"] = [{"name": "project_tests", "state": "passed", "sha": "a" * 40,
            "explanation": "FAKE fixture: configured test passed.", "artifact_ids": []}]
    if status == "failed":
        state["details"]["reason_code"] = "worker_launch_failed" if name == "failed_launch" else "acceptance_failed"
        state["snapshot"]["error"] = "Offline example of a failed run."
    if status in ("paused", "stopped"):
        state["details"]["controls"]["pause" if status == "paused" else "stop"].update(
            requested=True, acknowledged=True, request_sequence=1, acknowledged_at=stamp)
        state["details"]["controls"].update(last_published_sequence=1, last_processed_sequence=1)
    if name == "uncertain_correction_delivery":
        state["details"]["controls"]["corrections"] = [{"id": "correction-1", "text": "Please retain existing behavior.",
            "sequence": 1, "created_at": stamp, "a": {"state": "delivered", "call_id": "call-a", "updated_at": stamp},
            "b": {"state": "delivery_uncertain", "call_id": "call-b", "updated_at": stamp}}]
        state["details"]["controls"].update(last_published_sequence=1, last_processed_sequence=1)
    if name != "missing_usage" and status == "completed":
        state["details"]["usage"].update(known_calls=2, total_calls=2, cached_input_a=3, cached_input_b=3)
        state["snapshot"].update(reported_tokens_a=18, reported_tokens_b=18)
    else:
        state["details"]["usage"]["total_calls"] = 2 if status == "running" else 0
    response = {"schema_version": 1, "generation": 3, "snapshot": _snapshot(state["snapshot"]),
        "details": state["details"], "corrections": _corrections(state), "artifacts": state["artifacts"],
        "events": [AgentEvent(state["snapshot"]["run_id"], who, stamp, "message", f"FAKE account {who.upper()} fixture activity", {"schema_version": 1, "stage": state["snapshot"]["stage"]}) for who in ("a", "b")],
        "next_offset": 0, "has_more": False}
    return deepcopy(validate_response(PollResponse, response))

def representative_fixtures(root: Path | None = None) -> dict[str, PollResponse]:
    return {name: poll_fixture(name, root) for name in FIXTURE_NAMES}

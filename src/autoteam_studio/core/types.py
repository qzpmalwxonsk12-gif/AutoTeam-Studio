from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, get_type_hints, get_origin, get_args, Union
import types
from datetime import datetime
import re

from autoteam_studio.contracts import AgentEvent, Correction, RunSnapshot


class ControllerError(RuntimeError):
    def __init__(self, code: str, message: str, run_id: str | None = None):
        from .safety import sanitize
        self.code, self.message, self.run_id = code, sanitize(message), run_id
        super().__init__(self.message)


@dataclass(frozen=True)
class ControllerSettings:
    backend: Literal["real", "fake"] = "real"
    git_executable: Path | None = None
    codex_executable: Path | None = None
    vscode_executable: Path | None = None
    account_a_home: Path = Path.home() / ".codex-a"
    account_b_home: Path = Path.home() / ".codex-b"
    storage_root: Path | None = None
    worker_executable: Path | None = None
    test_timeout_seconds: int = 600

    def __post_init__(self):
        if self.backend not in ("real", "fake"):
            raise ValueError("backend must be real or fake")
        if type(self.test_timeout_seconds) is not int or not 1 <= self.test_timeout_seconds <= 7200:
            raise ValueError("test_timeout_seconds must be an integer from 1 through 7200")
        for name in ("git_executable", "codex_executable", "vscode_executable", "worker_executable"):
            value = getattr(self, name)
            if value is not None:
                value = Path(value)
                if not value.is_absolute() or not value.is_file():
                    raise ValueError(f"{name} must identify an absolute executable file")
                object.__setattr__(self, name, value.resolve())
        for name in ("account_a_home", "account_b_home", "storage_root"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value).resolve())
        if self.account_a_home == self.account_b_home or (
            self.account_a_home.exists() and self.account_b_home.exists()
            and self.account_a_home.samefile(self.account_b_home)
        ):
            raise ValueError("Account homes must resolve to distinct directories")


class DependencyInfo(TypedDict):
    available: bool
    detail: str
    path: str | None

class Dependencies(TypedDict):
    git: DependencyInfo
    vscode: DependencyInfo
    codex: DependencyInfo
    runtime: DependencyInfo
    account_a: DependencyInfo
    account_b: DependencyInfo
    worker: DependencyInfo

class Artifact(TypedDict):
    id: str
    label: str
    kind: str
    path: str

class GateEvidence(TypedDict):
    name: str
    state: Literal["passed", "failed", "not_run"]
    sha: str | None
    explanation: str
    artifact_ids: list[str]

class CheckpointControl(TypedDict):
    requested: bool
    acknowledged: bool
    request_sequence: int | None
    acknowledged_at: str | None

class Submission(TypedDict):
    state: Literal["pending", "submitting", "delivered", "delivery_uncertain"]
    call_id: str | None
    updated_at: str

class CorrectionDelivery(TypedDict):
    id: str
    text: str
    sequence: int
    created_at: str
    a: Submission
    b: Submission

class Controls(TypedDict):
    last_published_sequence: int
    last_processed_sequence: int
    pause: CheckpointControl
    stop: CheckpointControl
    corrections: list[CorrectionDelivery]

class ActiveCall(TypedDict):
    account: Literal["a", "b"]
    call_id: str
    stage: str
    started_at: str
    deadline: str
    last_activity_at: str | None

class Worktrees(TypedDict):
    a: str | None
    b: str | None
    result: str | None

class UsageCoverage(TypedDict):
    known_calls: int
    total_calls: int
    cached_input_a: int | None
    cached_input_b: int | None

class RunDetails(TypedDict):
    schema_version: int
    generation: int
    backend: Literal["real", "fake"]
    project_root: str
    project_identity: str
    source_run_id: str | None
    base_sha: str | None
    result_sha: str | None
    result_branch: str | None
    worktrees: Worktrees
    reason_code: str | None
    gates: list[GateEvidence]
    controls: Controls
    active_calls: list[ActiveCall]
    usage: UsageCoverage

class PollResponse(TypedDict):
    schema_version: int
    generation: int
    snapshot: RunSnapshot
    details: RunDetails
    corrections: list[Correction]
    artifacts: list[Artifact]
    events: list[AgentEvent]
    next_offset: int
    has_more: bool

class TestAuthorization(TypedDict):
    schema_version: int
    inspection_id: str
    project_identity: str
    commands: list[list[str]]
    working_directory: Literal["result_worktree"]
    environment_policy: Literal["sanitized_inherited_v1"]
    timeout_seconds: int
    execution: Literal["sequential"]
    authorized: bool
    authorization_id: str | None
    granted_at: str | None
    notice: str

def validate_response(schema, value):
    """Reject missing/extra keys and incorrect types at the facade boundary."""
    def check(t, v, label):
        origin, args = get_origin(t), get_args(t)
        if origin in (Union, types.UnionType):
            for option in args:
                try:
                    check(option, v, label)
                    return
                except ValueError:
                    pass
            raise ValueError(f"Invalid {label}")
        if origin is Literal:
            if v not in args or type(v) not in {type(x) for x in args}:
                raise ValueError(f"Invalid {label}")
        elif origin is list:
            if not isinstance(v, list):
                raise ValueError(f"Invalid {label}")
            for item in v:
                check(args[0], item, label)
        elif hasattr(t, "__required_keys__"):
            hints = get_type_hints(t)
            if not isinstance(v, dict) or v.keys() != hints.keys():
                raise ValueError(f"Invalid keys for {label}")
            for k, item in v.items():
                check(hints[k], item, k)
        elif t in (int, bool, str, type(None)):
            if type(v) is not t:
                raise ValueError(f"Invalid {label}")
            if t is int and v < 0:
                raise ValueError(f"Negative {label}")
            if t is int and label == "schema_version" and v != 1:
                raise ValueError("Unsupported schema version")
            if t is str and label in ("path", "project_root", "a", "b", "result") and not Path(v).is_absolute():
                raise ValueError(f"Relative {label}")
            if t is str and label in ("acknowledged_at", "updated_at", "created_at", "started_at", "deadline", "last_activity_at", "granted_at"):
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", v):
                    raise ValueError(f"Invalid UTC timestamp for {label}")
                try:
                    datetime.fromisoformat(v.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError(f"Invalid UTC timestamp for {label}") from None
        elif not isinstance(v, t):
            raise ValueError(f"Invalid {label}")
    check(schema, value, schema.__name__)
    if schema is PollResponse and value["generation"] != value["details"]["generation"]:
        raise ValueError("Poll state generations disagree")
    return value

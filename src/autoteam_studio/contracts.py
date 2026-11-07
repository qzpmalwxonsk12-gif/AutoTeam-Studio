"""Shared public data contracts — keep stable during independent agent implementation.

Core/worker implementation belongs to Agent A. UI belongs to Agent B.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Any

AgentId = Literal["a", "b", "system"]
RunStatus = Literal["queued", "running", "paused", "completed", "failed", "stopped"]


@dataclass(frozen=True)
class RunSpec:
    project_root: Path
    prompt: str
    revision: bool = False
    test_commands: tuple[str, ...] = ()
    max_call_seconds: int = 1200
    review_rounds: int = 1

    def __post_init__(self) -> None:
        if not str(self.prompt).strip():
            raise ValueError("A nonempty project prompt is required")
        if self.max_call_seconds < 30 or self.max_call_seconds > 7200:
            raise ValueError("max_call_seconds must be between 30 and 7200")
        if not 1 <= self.review_rounds <= 4:
            raise ValueError("review_rounds must be between 1 and 4")


@dataclass(frozen=True)
class AgentEvent:
    run_id: str
    agent: AgentId
    timestamp: str
    kind: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    status: RunStatus
    stage: str = ""
    result_dir: Path | None = None
    error: str | None = None
    reported_tokens_a: int | None = None
    reported_tokens_b: int | None = None


@dataclass(frozen=True)
class Correction:
    id: str
    text: str
    delivered_a: bool = False
    delivered_b: bool = False

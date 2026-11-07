"""Bounded background calls with a reserved control lane.

The GUI acknowledges completion before a lane accepts another job. Thus even
when the event loop is busy there can be at most one queued poll result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot, Qt


@dataclass(frozen=True)
class Reply:
    operation: str
    identity: tuple
    value: Any = None
    error: Exception | None = None


class _Signals(QObject):
    finished = Signal(object)


class _Job(QRunnable):
    def __init__(self, operation, identity, call):
        super().__init__()
        self.operation, self.identity, self.call = operation, identity, call
        self.signals = _Signals()

    @Slot()
    def run(self):
        try:
            reply = Reply(self.operation, self.identity, self.call())
        except Exception as exc:
            reply = Reply(self.operation, self.identity, error=exc)
        self.signals.finished.emit(reply)


class BackgroundAdapter(QObject):
    completed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pools = {name: QThreadPool(self) for name in ("work", "control", "chat")}
        for pool in self.pools.values():
            pool.setMaxThreadCount(1)
        self.jobs: dict[str, _Job] = {}

    @property
    def busy(self):
        return bool(self.jobs)

    def submit(self, operation: str, identity: tuple, call: Callable,
               *, control: bool = False, chat: bool = False) -> bool:
        lane = "chat" if chat else "control" if control else "work"
        if lane in self.jobs:
            return False
        job = _Job(operation, identity, call)
        self.jobs[lane] = job
        job.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self.pools[lane].start(job)
        return True

    @Slot(object)
    def _finished(self, reply):
        lane = next(name for name, job in self.jobs.items()
                    if job.operation == reply.operation and job.identity == reply.identity)
        del self.jobs[lane]
        self.completed.emit(reply)

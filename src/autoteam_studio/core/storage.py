from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
import uuid

from autoteam_studio.contracts import AgentEvent
from .safety import sanitize
from .types import ControllerError
from .deadlines import remaining

TERMINAL = {"completed", "failed", "stopped"}
RETENTION_LIMIT = 256 * 1024 * 1024
EVENT_LIMIT = 128 * 1024
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

def event_payload(payload):
    """The same allowlist applies at publication and when reading disk records."""
    allowed = {"schema_version", "stage", "call_id", "state", "exit_code", "paths", "output", "input_tokens", "output_tokens", "cached_input_tokens", "report_sequence", "code", "artifact_id", "account", "status", "reason"}
    def clean(value):
        if isinstance(value, str): return sanitize(value)
        if isinstance(value, list): return [clean(item) for item in value[:100]]
        if value is None or type(value) in (int, bool): return value
        return None
    return {key: clean(value) for key, value in payload.items() if key in allowed}

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

def io_path(path: Path) -> Path:
    """Extended Windows spelling for file I/O in long selected workspace paths."""
    if os.name == "nt" and not str(path).startswith("\\\\?\\"):
        absolute = str(path.absolute())
        return Path("\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute)
    return path

def atomic(path: Path, value) -> None:
    remaining()
    path = io_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(".~" + uuid.uuid4().hex[:16])
    try:
        with temp.open("xb") as file:
            remaining()
            file.write(encode(value)); file.flush(); os.fsync(file.fileno())
        deadline = time.monotonic() + .5
        while True:
            remaining()
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                # Windows readers briefly deny replacement without SHARE_DELETE.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.005)
    except OSError as exc:
        raise ControllerError("persistence_failure", "Cannot durably publish run data; storage may be unavailable.") from exc
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            # Preserve the publication error if storage also refuses cleanup.
            pass

def read_json(path: Path):
    remaining()
    try:
        deadline = time.monotonic() + .5
        while True:
            remaining()
            try:
                with io_path(path).open("rb") as file:
                    data = file.read(8 * 1024 * 1024 + 1)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.005)
        if len(data) > 8 * 1024 * 1024:
            raise ControllerError("storage_corrupt", "Persisted state exceeds its size limit.")
        return json.loads(data)
    except (OSError, ValueError) as exc:
        raise ControllerError("storage_unavailable", "Run storage is unavailable or damaged; recovery is required.") from exc

@contextmanager
def guard(path: Path, timeout: float = 1):
    timeout = remaining(timeout)
    path = path.resolve()
    key = str(path)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    deadline = time.monotonic() + timeout
    if not lock.acquire(timeout=timeout):
        raise ControllerError("operation_timeout", "Timed out acquiring publication guard.")
    file = None
    acquired = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open("a+b")
        if path.stat().st_size == 0:
            file.write(b"0"); file.flush()
        while True:
            remaining()
            try:
                file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True; break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ControllerError("operation_timeout", "Timed out acquiring publication guard.")
                time.sleep(.01)
        yield
    finally:
        try:
            if file:
                try:
                    if acquired:
                        file.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
                finally:
                    file.close()
        finally:
            lock.release()

def namespace() -> Path:
    from .safety import check_local_volume
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "AutoTeamStudio/core"
    check_local_volume(base)
    base.mkdir(parents=True, exist_ok=True)
    return base

class Store:
    def __init__(self, root: Path):
        self.root = root
        self.lock = root / "publication.lock"

    def read(self):
        pointer = read_json(self.root / "current.json")
        return read_json(self.root / "generations" / f"{pointer['generation']:012}.json")

    def capacity(self, extra=0):
        total = 0
        deadline = time.monotonic() + 2
        for directory, dirs, files in os.walk(self.root):
            remaining()
            dirs[:] = [d for d in dirs if d != "worktrees"]
            for name in files:
                remaining()
                total += (Path(directory) / name).stat().st_size
                if total + extra > RETENTION_LIMIT:
                    raise ControllerError("retention_limit", "Run diagnostics exceeded the 256 MiB retention limit.")
                if time.monotonic() > deadline:
                    raise ControllerError("operation_timeout", "Diagnostics capacity inspection exceeded its deadline.")

    def publish(self, state):
        state["generation"] += 1
        state["details"]["generation"] = state["generation"]
        state["high_water"] = (self.root / "events.jsonl").stat().st_size if (self.root / "events.jsonl").exists() else 0
        self.capacity(len(encode(state)))
        atomic(self.root / "generations" / f"{state['generation']:012}.json", state)
        atomic(self.root / "current.json", {"schema_version": 1, "generation": state["generation"]})

    def transaction(self, change):
        with guard(self.lock):
            state = self.read()
            if state["snapshot"]["status"] in TERMINAL:
                raise ControllerError("run_terminal", "Run is terminal; no more mutations are accepted.", state["snapshot"]["run_id"])
            result = change(state)
            self.publish(state)
            return result

    def artifact(self, state, label: str, content: str, kind="text") -> str:
        content = sanitize(content)
        if len(content.encode("utf-8")) > 4 * 1024 * 1024:
            raise ControllerError("output_limit", "Sanitized artifact exceeds 4 MiB.")
        self.capacity(len(content.encode("utf-8")))
        aid = uuid.uuid4().hex
        path = self.root / "evidence" / f"{aid}.txt"
        # Atomic JSON text artifacts are readable and exclude unrestricted payloads.
        atomic(path, {"label": sanitize(label), "text": content})
        state["artifacts"].append({"id": aid, "label": sanitize(label), "kind": kind, "path": str(path.resolve())})
        return aid

    def event(self, state, agent, kind, message, payload=None):
        payload = event_payload(payload or {})
        payload.update(schema_version=1, stage=state["snapshot"]["stage"])
        event = asdict(AgentEvent(state["snapshot"]["run_id"], agent, now(), kind, sanitize(message), payload))
        data = encode(event)
        if len(data) > EVENT_LIMIT:
            aid = self.artifact(state, "Oversized sanitized activity", event["message"])
            event["message"] = "Large sanitized activity saved as an artifact."
            event["payload"] = {"schema_version": 1, "stage": state["snapshot"]["stage"], "artifact_id": aid}
            data = encode(event)
        self.capacity(len(data))
        with io_path(self.root / "events.jsonl").open("ab") as file:
            file.write(data); file.flush(); os.fsync(file.fileno())

def read_events(root: Path, run_id: str, offset: int, high_water: int):
    if type(offset) is not int or offset < 0 or offset > high_water:
        raise ValueError("Event cursor is outside the published generation")
    path = root / "events.jsonl"
    if not path.exists():
        if high_water:
            raise ControllerError("storage_corrupt", "Published activity log is missing.")
        if offset: raise ValueError("Invalid event cursor")
        return [], 0, False
    events, used, scanned = [], 0, 0
    with io_path(path).open("rb") as file:
        if file.seek(0, os.SEEK_END) < high_water:
            raise ControllerError("storage_corrupt", "Activity log is shorter than the published generation cursor.")
        if offset:
            file.seek(offset - 1)
            if file.read(1) != b"\n": raise ValueError("Cursor must be a record boundary")
        file.seek(offset)
        while file.tell() < high_water and len(events) < 200 and scanned < 512:
            remaining()
            start = file.tell()
            line = file.readline(min(EVENT_LIMIT + 1, high_water - start))
            if not line.endswith(b"\n"):
                if len(line) > EVENT_LIMIT:
                    raise ControllerError("storage_corrupt", "Oversized persisted event requires recovery.")
                break
            if used + len(line) > 256 * 1024:
                file.seek(start); break
            scanned += 1; used += len(line); offset = file.tell()
            try:
                raw = json.loads(line)
                if set(raw) != {"run_id", "agent", "timestamp", "kind", "message", "payload"} or raw["run_id"] != run_id or raw["agent"] not in ("a", "b", "system") or not isinstance(raw["payload"], dict):
                    raise ValueError()
                if any(not isinstance(raw[k], str) for k in ("timestamp", "kind", "message")):
                    raise ValueError()
                raw["message"] = sanitize(raw["message"])
                raw["kind"] = sanitize(raw["kind"])
                raw["timestamp"] = sanitize(raw["timestamp"])
                raw["payload"] = event_payload(raw["payload"])
                events.append(AgentEvent(**raw))
            except (ValueError, TypeError):
                # Consume damaged complete records with a bounded diagnostic, never raw bytes.
                events.append(AgentEvent(run_id, "system", now(), "error", "Malformed persisted activity record skipped.", {"schema_version": 1, "stage": "diagnostics", "code": "malformed_event"}))
    return events, offset, offset < high_water

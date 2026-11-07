"""Frozen production facade for the GUI. All potentially blocking methods belong
on the UI's background adapter; none imports Qt or consumes model usage on reads.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

from autoteam_studio.contracts import AgentEvent, Correction, RunSnapshot, RunSpec
from .types import (ControllerSettings, ControllerError, DependencyInfo, Dependencies, Artifact,
                    GateEvidence, CheckpointControl, Submission, CorrectionDelivery, Controls,
                    ActiveCall, Worktrees, UsageCoverage, RunDetails, PollResponse, TestAuthorization,
                    validate_response)
from .safety import (resolve_launcher, resolve_tests, contained_path, sanitize, check_local_volume)
from .storage import Store, atomic, read_json, guard, namespace, now, TERMINAL, read_events, encode, io_path
from .project import inspect_project, prepare, fingerprint, git
from .process import OwnedProcess, alive, own_identity
from .deadlines import bounded, remaining, cleanup_budget

PROTOCOL_VERSION = 1

def fake_demo_commands() -> tuple[str, ...]:
    """Explicit app-owned offline test. Inspect and grant it like any command."""
    if getattr(sys, "frozen", False):
        worker = Path(sys.executable).parent / "worker/AutoTeamStudioWorker.exe"
        return (f'"{worker}" --fake-test',)
    script = Path(__file__).resolve().parent.parent / "worker/fake_cli.py"
    return (f'"{sys.executable}" "{script}" --test',)

def _snapshot(raw):
    return RunSnapshot(**{**raw, "result_dir": Path(raw["result_dir"]) if raw["result_dir"] else None})

def _corrections(state):
    return [Correction(c["id"], c["text"], c["a"]["state"] == "delivered", c["b"]["state"] == "delivered")
            for c in state["details"]["controls"]["corrections"]]

def initial_state(run_id, root, project_id, backend, source=None):
    checkpoint = {"requested": False, "acknowledged": False, "request_sequence": None, "acknowledged_at": None}
    return {"schema_version": 1, "generation": 0, "high_water": 0,
            "snapshot": asdict(RunSnapshot(run_id, "queued", "queued")), "artifacts": [],
            "requests": [], "usage_calls": {}, "execution_processes": [], "accepted": False,
            "details": {"schema_version": 1, "generation": 0, "backend": backend,
                "project_root": str(root), "project_identity": project_id, "source_run_id": source,
                "base_sha": None, "result_sha": None, "result_branch": None,
                "worktrees": {"a": None, "b": None, "result": None}, "reason_code": None,
                "gates": [], "controls": {"last_published_sequence": 0, "last_processed_sequence": 0,
                    "pause": checkpoint.copy(), "stop": checkpoint.copy(), "corrections": []},
                "active_calls": [], "usage": {"known_calls": 0, "total_calls": 0, "cached_input_a": None, "cached_input_b": None}}}

class TeamController:
    def __init__(self, settings: ControllerSettings | None = None):
        self._settings = settings or ControllerSettings()
        self._namespace = namespace()
        self._workers = {}
        from .chat import ChatService
        self._chat = ChatService(self._settings, self._namespace)

    def open_chat(self, identity=None, new=False):
        return self._chat.open(identity, new)

    def read_chat(self, identity):
        return self._chat.read(identity)

    def chat_turn(self, identity, text, casual=False):
        return self._chat.send(identity, text, casual=casual)

    def cancel_chat(self):
        self._chat.cancel()

    def link_chat_run(self, identity, run_id):
        store, _ = self._lookup(run_id)
        state = store.read()
        manifest = read_json(store.root / "manifest.json")
        snapshot, details = state["snapshot"], state["details"]
        summary = (f"Project task {run_id}: {snapshot['status']}.\n"
                   f"Project: {details['project_root']}\nRequested task: {manifest['prompt']}\n"
                   f"Stage: {snapshot['stage']}\nResult: {snapshot['result_dir'] or 'Not available yet'}\n"
                   f"Outcome: {snapshot['error'] or details['reason_code'] or snapshot['status']}\n")
        summary += "\n".join(f"{g['name']}: {g['state']}" for g in details["gates"])
        return self._chat.record_run(identity, run_id, sanitize(summary), snapshot["status"])

    @property
    def settings(self):
        return self._settings

    def _index(self):
        path = self._namespace / "index.json"
        return read_json(path) if path.exists() else {"schema_version": 1, "runs": {}}

    def _lookup(self, run_id: str) -> tuple[Store, dict]:
        record = self._index()["runs"].get(run_id)
        if not record:
            raise ControllerError("run_not_found", "Run was not found in the durable index.", run_id)
        store = Store(Path(record["path"]))
        return store, record

    def _git(self):
        command = resolve_launcher(self.settings.git_executable, "git")
        if not command or len(command) != 1:
            raise ControllerError("git_unavailable", "Install Git or select its native executable.")
        return command[0]

    @bounded(10)
    def check_dependencies(self) -> Dependencies:
        result = {}
        for key, executable, name in (("git", self.settings.git_executable, "git"),
                                      ("vscode", self.settings.vscode_executable, "code"),
                                      ("codex", self.settings.codex_executable, "codex")):
            try:
                command = resolve_launcher(executable, name)
                result[key] = {"available": bool(command), "detail": "Detected" if command else f"Install {name} or select its executable.", "path": command[0] if command else None}
            except ControllerError as exc:
                result[key] = {"available": False, "detail": exc.message, "path": None}
        for who in ("a", "b"):
            path = getattr(self.settings, f"account_{who}_home")
            # Credential presence is only configuration evidence; never open it.
            configured = path.is_dir() and (path / "auth.json").is_file() if self.settings.backend == "real" else False
            result[f"account_{who}"] = {"available": configured or self.settings.backend == "fake",
                "detail": "Not required in fake mode" if self.settings.backend == "fake" else "Credential file present; identity and authentication not verified." if configured else "Log in to a distinct account home externally.",
                "path": str(path) if self.settings.backend == "real" else None}
        runtime_ok = sys.version_info[:2] == (3, 12) or bool(getattr(sys, "frozen", False))
        result["runtime"] = {"available": runtime_ok, "detail": "Python 3.12 required" if not runtime_ok else "Supported runtime", "path": str(Path(sys.executable).resolve())}
        worker = self._worker_command()
        result["worker"] = {"available": worker is not None, "detail": "App-owned worker available" if worker else "Bundled worker executable is missing.", "path": worker[0] if worker else None}
        return validate_response(Dependencies, result)

    def _worker_command(self):
        if self.settings.worker_executable:
            return resolve_launcher(self.settings.worker_executable, "worker")
        if getattr(sys, "frozen", False):
            path = Path(sys.executable).parent / "worker/AutoTeamStudioWorker.exe"
            return [str(path)] if path.is_file() else None
        return [sys.executable, "-m", "autoteam_studio.worker"]

    @bounded(60)
    def prepare_project(self, path: Path, create: bool = True) -> Path:
        if type(create) is not bool: raise ValueError("create must be a boolean")
        return prepare(self._git(), Path(path), create)

    @bounded(10)
    def select_project(self, path: Path) -> Path:
        from .setup import select_folder
        return select_folder(path)

    @bounded(15)
    def project_status(self, path: Path) -> dict:
        from .setup import project_status
        return project_status(self._git(), path)

    @bounded(60)
    def inspect_baseline(self, path: Path) -> dict:
        from .setup import inspect_baseline
        return inspect_baseline(self._git(), path)

    @bounded(60)
    def save_baseline(self, path: Path, inspection_id: str) -> Path:
        from .setup import save_baseline
        return save_baseline(self._git(), path, inspection_id)

    @bounded(30)
    def connect_accounts(self) -> dict:
        """Local saved-login checks only. Never starts a development worker."""
        from .codex import verify_login
        result = {}
        command = resolve_launcher(self.settings.codex_executable, "codex") if self.settings.backend == "real" else None
        for who in ("a", "b"):
            home = getattr(self.settings, f"account_{who}_home")
            info = {"available": False, "path": str(home), "detail": "Not checked"}
            if self.settings.backend == "fake":
                info.update(available=True, detail="Simulated account; no credentials used")
            elif not command:
                info["detail"] = "Codex CLI is unavailable. Select its executable in Settings."
            elif not (home / "auth.json").is_file():
                info["detail"] = "No saved login. Authenticate this home outside AutoTeam, then check again."
            else:
                try:
                    verify_login(command, home, self._namespace)
                    info.update(available=True, detail="Saved ChatGPT login verified locally; subscription identity is not inspected")
                except ControllerError as exc:
                    info["detail"] = exc.message
            result[f"account_{who}"] = info
        return result

    @bounded(10)
    def inspect_test_authorization(self, project_root: Path, commands: tuple[str, ...]) -> TestAuthorization:
        if not isinstance(commands, tuple) or any(not isinstance(c, str) for c in commands):
            raise ValueError("commands must be a tuple of command strings")
        _, _, project_id, _ = inspect_project(self._git(), Path(project_root))
        arrays = resolve_tests(commands)
        policy = {"schema_version": 1, "project_identity": project_id, "commands": arrays,
                  "working_directory": "result_worktree", "environment_policy": "sanitized_inherited_v1",
                  "timeout_seconds": self.settings.test_timeout_seconds, "execution": "sequential"}
        inspection_id = hashlib.sha256(encode(policy)).hexdigest()
        path = self._namespace / "authorizations" / f"{inspection_id}.json"
        grant = read_json(path) if io_path(path).exists() else None
        if grant and grant.get("inspection_id") != inspection_id:
            raise ControllerError("authorization_invalid", "Persisted authorization does not match the inspected policy.")
        answer = {**policy, "inspection_id": inspection_id, "authorized": bool(grant and arrays),
                  "authorization_id": grant["authorization_id"] if grant and arrays else None,
                  "granted_at": grant["granted_at"] if grant and arrays else None,
                  "notice": "These commands execute project code which agents may subsequently modify. Tests run sequentially in the result worktree with a sanitized inherited environment; this is an arbitrary-code execution boundary."}
        return validate_response(TestAuthorization, answer)

    @bounded(10)
    def grant_test_authorization(self, project_root: Path, commands: tuple[str, ...], inspection_id: str) -> TestAuthorization:
        inspected = self.inspect_test_authorization(project_root, commands)
        if inspected["inspection_id"] != inspection_id:
            raise ValueError("Inspection changed; inspect and authorize the exact current execution policy")
        if not inspected["commands"]:
            raise ValueError("Empty test commands cannot be authorized")
        with guard(self._namespace / "authorization.lock"):
            atomic(self._namespace / "authorizations" / f"{inspection_id}.json",
                   {"authorization_id": uuid.uuid4().hex, "granted_at": now(), "inspection_id": inspection_id})
        return self.inspect_test_authorization(project_root, commands)

    @bounded(30)
    def start(self, spec: RunSpec) -> str:
        if not isinstance(spec, RunSpec):
            raise ValueError("spec must be a RunSpec")
        if spec.revision:
            raise ValueError("Use start_revision with an explicit source run")
        return self._start(spec, None)

    @bounded(30)
    def start_revision(self, spec: RunSpec, source_run_id: str) -> str:
        if not isinstance(source_run_id, str) or not source_run_id.strip():
            raise ValueError("Revision requires a nonempty explicit source run ID")
        return self._start(spec, source_run_id)

    def _start(self, spec: RunSpec, source: str | None) -> str:
        deadline = time.monotonic() + 30
        if not isinstance(spec, RunSpec): raise ValueError("spec must be a RunSpec")
        if spec.prompt.strip().lower() in ("prompt", "enter prompt", "todo", "test", "..."):
            raise ValueError("Enter meaningful project instructions")
        executable = self._git()
        root, common, project_id, base = inspect_project(executable, spec.project_root)
        if self.settings.backend == "real":
            from .codex import resolve_accounts
            resolve_accounts(self.settings, root)
        authorization = self.inspect_test_authorization(root, spec.test_commands)
        if spec.test_commands and not authorization["authorized"]:
            raise ControllerError("tests_not_authorized", "Inspect and explicitly authorize the current project test commands before Start.")
        if source:
            old = self.run_details(source)
            if old["project_identity"] != project_id or not old["result_sha"] or self.snapshot(source).status != "completed":
                raise ValueError("Revision source must be a completed result in this repository")
            base = git(executable, root, "rev-parse", "--verify", old["result_sha"])
        original = fingerprint(executable, root, common)
        storage = self.settings.storage_root or root.parent / f".{root.name}-autoteam-{hashlib.sha256(str(root).encode()).hexdigest()[:12]}"
        storage = storage.resolve()
        check_local_volume(storage)
        if storage.is_relative_to(root) or storage.is_relative_to(common):
            raise ValueError("Run storage must be outside the project checkout and Git directory")
        if self.settings.backend == "real":
            for home in (self.settings.account_a_home, self.settings.account_b_home):
                if storage.is_relative_to(home) or home.is_relative_to(storage):
                    raise ControllerError("account_home_unsafe", "Run storage and account homes must not overlap.")
        command = self._worker_command()
        if not command:
            raise ControllerError("worker_unavailable", "Install the bundled worker executable.")
        run_id, token = uuid.uuid4().hex[:16], uuid.uuid4().hex
        run_root = storage / "runs" / run_id
        store = Store(run_root)
        reservation = self._namespace / "reservations" / f"{project_id}.json"
        record = {"token": token, "run_id": run_id, "launcher": own_identity(), "worker": None,
                  "handoff_deadline": time.time() + 15, "accepted": False}
        with guard(self._namespace / "index.lock"):
            if reservation.exists():
                previous = read_json(reservation)
                old_store, _ = self._lookup(previous["run_id"])
                old_state = old_store.read()
                if old_state["snapshot"]["status"] not in TERMINAL or alive(previous.get("worker")):
                    raise ControllerError("project_reserved", "This repository has an unfinished run. Reconcile it before starting another.")
            storage.mkdir(parents=True, exist_ok=True)
            identity_file = storage / "identity.json"
            if identity_file.exists() and read_json(identity_file)["project_identity"] != project_id:
                raise ControllerError("storage_identity_mismatch", "Storage belongs to a different repository.")
            atomic(identity_file, {"schema_version": 1, "project_identity": project_id})
            run_root.mkdir(parents=True)
            state = initial_state(run_id, root, project_id, self.settings.backend, source)
            state["details"]["base_sha"] = base
            state["launcher"] = record["launcher"]
            state["handoff_deadline"] = record["handoff_deadline"]
            settings = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(self.settings).items()}
            manifest = {"schema_version": 1, "run_id": run_id, "token": token, "project_root": str(root),
                        "common_dir": str(common), "project_identity": project_id, "base_sha": base,
                        "prompt": sanitize(spec.prompt), "source_run_id": source, "max_call_seconds": spec.max_call_seconds,
                        "review_rounds": spec.review_rounds, "settings": settings, "git": executable,
                        "authorization": authorization, "original": original, "namespace": str(self._namespace)}
            atomic(run_root / "manifest.json", manifest)
            store.publish(state)
            index = self._index()
            index["runs"][run_id] = {"path": str(run_root), "project_root": str(root), "project_identity": project_id, "created_at": now()}
            atomic(self._namespace / "index.json", index)
            atomic(reservation, record)
        process = None
        try:
            from .safety import sanitized_environment
            env = sanitized_environment()
            if not getattr(sys, "frozen", False):
                env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
            process = OwnedProcess([*command, "--run-dir", str(run_root), "--token", token, "--protocol", "1"], root, env)
            process.process.stdin.close()
            self._workers[run_id] = process
            handoff = min(deadline, time.monotonic() + 15)
            while time.monotonic() < handoff:
                remaining()
                current = store.read()
                if current.get("accepted"):
                    remaining()
                    return run_id
                if current["snapshot"]["status"] in TERMINAL:
                    raise ControllerError(current["details"]["reason_code"] or "worker_failed", current["snapshot"]["error"] or "Worker failed before acceptance.", run_id)
                if process.process.poll() is not None:
                    raise ControllerError("worker_launch_failed", "Worker exited before protocol handoff.", run_id)
                time.sleep(.025)
            raise ControllerError("worker_handshake_timeout", "Worker did not acknowledge protocol version 1 within 15 seconds.", run_id)
        except Exception as exc:
            error = exc if isinstance(exc, ControllerError) else ControllerError("worker_launch_failed", "Worker could not be launched.", run_id)
            error.run_id = run_id
            try:
                with cleanup_budget():
                    if process:
                        process.close()
                    self._workers.pop(run_id, None)
                    with guard(store.lock):
                        state = store.read()
                        if state["snapshot"]["status"] not in TERMINAL:
                            state["snapshot"].update(status="failed", stage="launch", error=error.message)
                            state["details"]["reason_code"] = error.code
                            store.event(state, "system", "error", error.message, {"code": error.code})
                            store.artifact(state, "Launch failure", error.message)
                            store.publish(state)
                    self._release(project_id, token)
            except ControllerError as cleanup_error:
                cleanup_error.run_id = run_id
                raise cleanup_error from error
            raise error

    def _release(self, project_id, token):
        with guard(self._namespace / "index.lock"):
            path = self._namespace / "reservations" / f"{project_id}.json"
            if path.exists() and read_json(path)["token"] == token:
                path.unlink()

    @bounded(3)
    def _control(self, run_id, kind, text=None):
        store, _ = self._lookup(run_id)
        cid = uuid.uuid4().hex
        def publish(state):
            controls = state["details"]["controls"]
            sequence = controls["last_published_sequence"] + 1
            request = {"id": cid, "sequence": sequence, "kind": kind, "text": text, "created_at": now()}
            atomic(store.root / "requests" / f"{sequence:012}.json", request)
            state["requests"].append(request)
            controls["last_published_sequence"] = sequence
            if kind in ("pause", "stop"):
                controls[kind].update(requested=True, request_sequence=sequence, acknowledged=False, acknowledged_at=None)
            if kind == "correction":
                submission = {"state": "pending", "call_id": None, "updated_at": request["created_at"]}
                controls["corrections"].append({"id": cid, "text": text, "sequence": sequence,
                    "created_at": request["created_at"], "a": submission.copy(), "b": submission.copy()})
            store.event(state, "system", "correction" if kind == "correction" else "status", f"{kind.capitalize()} requested", {"state": "pending"})
        store.transaction(publish)
        return cid

    def queue_correction(self, run_id: str, text: str) -> str:
        if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 16 * 1024:
            raise ValueError("Correction must contain 1 through 16384 bytes of text")
        return self._control(run_id, "correction", sanitize(text.strip()))

    def pause_next_tasks(self, run_id: str) -> None:
        self._control(run_id, "pause")

    def resume(self, run_id: str) -> None:
        self._control(run_id, "resume")

    def stop_at_checkpoint(self, run_id: str) -> None:
        self._control(run_id, "stop")

    @bounded(60)
    def resume_stopped(self, run_id: str) -> str:
        """Restart only a verified orderly checkpoint; retain the run and its history."""
        store, _ = self._lookup(run_id)
        state = store.read()
        if state["snapshot"]["status"] != "stopped" or state.get("resume_point") is None:
            raise ControllerError("resume_unavailable", "Only orderly checkpoint stops can resume automatically. Interrupted writes need manual inspection.", run_id)
        if alive(state.get("worker")) or any(alive(p) for p in state.get("execution_processes", [])):
            raise ControllerError("worker_still_exiting", "The stopped worker is still exiting. Try resume again in a moment.", run_id)
        manifest = read_json(store.root / "manifest.json")
        executable = manifest["git"]
        root, common = Path(manifest["project_root"]), Path(manifest["common_dir"])
        if fingerprint(executable, root, common) != manifest["original"]:
            raise ControllerError("original_checkout_changed", "The original project changed since the stop; automatic resume is blocked.", run_id)
        for who, expected in state["resume_point"].items():
            path = Path(state["details"]["worktrees"][who])
            if fingerprint(executable, path, common) != expected:
                raise ControllerError("checkpoint_changed", f"Agent {who} worktree changed after stopping. Inspect it in VS Code before continuing.", run_id)
        if state.get("journal", {}).get("inflight"):
            raise ControllerError("resume_unavailable", "An interrupted operation has no completion record.", run_id)
        for step, digest in state.get("journal", {}).get("done", {}).items():
            if hashlib.sha256(encode(read_json(store.root / "steps" / f"{step}.json"))).hexdigest() != digest:
                raise ControllerError("checkpoint_changed", "Saved completed-step evidence changed.", run_id)
        # Use the saved launch settings so a backend toggle cannot turn a demo into a real run.
        settings = ControllerSettings(**manifest["settings"])
        launch_controller = TeamController(settings)
        command = launch_controller._worker_command()
        if not command:
            raise ControllerError("worker_unavailable", "The saved worker executable is unavailable.", run_id)
        project_id = manifest["project_identity"]
        reservation = self._namespace / "reservations" / f"{project_id}.json"
        token = uuid.uuid4().hex
        record = {"token": token, "run_id": run_id, "launcher": own_identity(), "worker": None,
                  "handoff_deadline": time.time() + 15, "accepted": False}
        with guard(self._namespace / "index.lock"):
            if reservation.exists():
                previous = read_json(reservation)
                other, _ = self._lookup(previous["run_id"])
                if other.read()["snapshot"]["status"] not in TERMINAL or alive(previous.get("worker")):
                    raise ControllerError("project_reserved", "Another run is using this project.", run_id)
            with guard(store.lock):
                fresh = store.read()
                if fresh["generation"] != state["generation"]:
                    raise ControllerError("checkpoint_changed", "The selected checkpoint changed. Refresh and retry.", run_id)
                manifest["token"] = token
                atomic(store.root / "manifest.json", manifest)
                state.update(accepted=False, worker=None, launcher=record["launcher"], handoff_deadline=record["handoff_deadline"], execution_processes=[])
                state["snapshot"].update(status="queued", stage="resuming", error=None)
                state["details"]["reason_code"] = None
                for name in ("pause", "stop"):
                    state["details"]["controls"][name].update(requested=False, acknowledged=False, request_sequence=None, acknowledged_at=None)
                state["details"]["controls"]["last_processed_sequence"] = state["details"]["controls"]["last_published_sequence"]
                store.event(state, "system", "status", "Resuming verified checkpoint; completed operations will be reused.")
                store.publish(state)
                atomic(reservation, record)
        process = None
        try:
            from .safety import sanitized_environment
            env = sanitized_environment()
            if not getattr(sys, "frozen", False):
                env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
            process = OwnedProcess([*command, "--run-dir", str(store.root), "--token", token, "--protocol", "1"], root, env)
            process.process.stdin.close()
            self._workers[run_id] = process
            while time.time() < record["handoff_deadline"]:
                remaining()
                current = store.read()
                if current.get("accepted"):
                    return run_id
                if process.process.poll() is not None:
                    break
                time.sleep(.025)
            raise ControllerError("worker_handshake_timeout", "Resuming worker did not acknowledge within 15 seconds.", run_id)
        except Exception:
            with cleanup_budget():
                if process:
                    process.close()
                with guard(store.lock):
                    current = store.read()
                    if not current.get("accepted"):
                        current["snapshot"].update(status="stopped", stage="stopped", error="Resume launch failed; checkpoint retained. Retry after checking the worker installation.")
                        store.publish(current)
                self._release(project_id, token)
            raise

    @bounded(5)
    def poll(self, run_id: str, offset: int) -> PollResponse:
        store, _ = self._lookup(run_id)
        self._recover(store)
        state = store.read()
        events, cursor, more = read_events(store.root, run_id, offset, state["high_water"])
        response = {"schema_version": 1, "generation": state["generation"], "snapshot": _snapshot(state["snapshot"]),
                    "details": state["details"], "corrections": _corrections(state), "artifacts": state["artifacts"],
                    "events": events, "next_offset": cursor, "has_more": more}
        return validate_response(PollResponse, response)

    def snapshot(self, run_id: str) -> RunSnapshot:
        return self.poll(run_id, 0)["snapshot"]

    def run_details(self, run_id: str) -> RunDetails:
        return self.poll(run_id, 0)["details"]

    def corrections(self, run_id: str) -> list[Correction]:
        return self.poll(run_id, 0)["corrections"]

    def artifacts(self, run_id: str) -> list[Artifact]:
        return self.poll(run_id, 0)["artifacts"]

    def events_since(self, run_id: str, offset: int) -> tuple[list[AgentEvent], int]:
        response = self.poll(run_id, offset)
        return response["events"], response["next_offset"]

    def list_history(self, project_root: Path) -> list[RunSnapshot]:
        return self.reconcile(project_root)

    @bounded(10)
    def reconcile(self, project_root: Path | None = None) -> list[RunSnapshot]:
        deadline = time.monotonic() + 10
        project_id = None
        if project_root:
            project_id = inspect_project(self._git(), project_root, require_clean=False)[2]
        result = []
        for run_id, record in reversed(list(self._index()["runs"].items())):
            remaining()
            if time.monotonic() >= deadline:
                raise ControllerError("operation_timeout", "History recovery exceeded its batch deadline; all runs remain indexed.")
            if project_id and record["project_identity"] != project_id:
                continue
            store = Store(Path(record["path"]))
            self._recover(store)
            result.append(_snapshot(store.read()["snapshot"]))
        return result

    def _recover(self, store):
        state = store.read()
        if state["snapshot"]["status"] in TERMINAL:
            return
        if state.get("accepted"):
            if alive(state.get("worker")): return
        elif time.time() < state.get("handoff_deadline", 0) and alive(state.get("launcher")):
            return
        if any(alive(p) for p in state.get("execution_processes", [])):
            raise ControllerError("recovery_blocked", "Recorded execution descendants are still alive; automatic takeover is blocked.", state["snapshot"]["run_id"])
        manifest = read_json(store.root / "manifest.json")
        preservation_error = None
        try:
            original = fingerprint(manifest["git"], Path(manifest["project_root"]), Path(manifest["common_dir"]))
            if original != manifest["original"]:
                preservation_error = ControllerError("original_checkout_changed",
                    "Original checkout or shared configuration changed. User files were preserved; inspect recovery evidence before continuing.")
        except ControllerError as exc:
            preservation_error = ControllerError("original_checkout_verification_failed",
                "Recovery could not verify preservation of the original checkout. " + exc.message)
        with guard(store.lock):
            state = store.read()
            if state["snapshot"]["status"] in TERMINAL or alive(state.get("worker")): return
            if any(alive(p) for p in state.get("execution_processes", [])):
                raise ControllerError("recovery_blocked", "Execution descendants remain alive; recovery is blocked.", state["snapshot"]["run_id"])
            for correction in state["details"]["controls"]["corrections"]:
                for who in ("a", "b"):
                    if correction[who]["state"] == "submitting":
                        correction[who].update(state="delivery_uncertain", updated_at=now())
            path = store.root / "events.jsonl"
            if path.exists() and path.stat().st_size > state["high_water"]:
                # Preserve the tail as evidence metadata; never concatenate a partial record.
                with path.open("r+b") as file:
                    file.seek(state["high_water"])
                    tail = file.read(1024 * 1024)
                    store.artifact(state, "Interrupted activity tail", f"{len(tail)} unpublished bytes excluded from coherent activity.")
                    file.truncate(state["high_water"]); file.flush(); os.fsync(file.fileno())
            reason = preservation_error.code if preservation_error else "worker_died"
            message = preservation_error.message if preservation_error else "Worker exited unexpectedly; partial evidence and worktrees were preserved."
            state["snapshot"].update(status="failed", stage="recovery", error=message)
            state["details"]["reason_code"] = reason
            state["details"]["active_calls"] = []
            store.artifact(state, "Recovery report", state["snapshot"]["error"])
            store.event(state, "system", "error", state["snapshot"]["error"], {"code": reason})
            store.publish(state)
        self._release(manifest["project_identity"], manifest["token"])

    @bounded(5)
    def open_in_vscode(self, path: Path) -> None:
        root = Path(path).resolve(strict=True)
        command = resolve_launcher(self.settings.vscode_executable, "code")
        if not command:
            raise ControllerError("vscode_unavailable", "Install VS Code or select its executable before opening.")
        import subprocess
        from .safety import sanitized_environment
        subprocess.Popen([*command, str(root)], shell=False, env=sanitized_environment(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=0x08000000 if os.name == "nt" else 0)

    @bounded(5)
    def open_artifact(self, run_id: str, artifact_id: str) -> None:
        store, _ = self._lookup(run_id)
        item = next((a for a in self.artifacts(run_id) if a["id"] == artifact_id), None)
        if not item:
            raise ValueError("Unknown artifact ID")
        path = contained_path(store.root / "evidence", Path(item["path"]))
        self.open_in_vscode(path)

# Public fixture helpers live here so UI imports need no backend-internal modules.
from .fixtures import FIXTURE_NAMES, poll_fixture, representative_fixtures

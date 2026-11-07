from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

from autoteam_studio.core.storage import Store, guard, read_json, atomic, now, TERMINAL
from autoteam_studio.core.process import execute, own_identity
from autoteam_studio.core.safety import sanitized_environment
from autoteam_studio.core.project import git, fingerprint, scan_commits, inspect_project
from autoteam_studio.core.events import normalize, record_usage
from autoteam_studio.core.types import ControllerError
from .gates import substantive, validate_review, verify_disjoint, verify_repair_paths
from .gates import review_schema
from autoteam_studio.core.codex import invocation, resolve_accounts, verify_login
from autoteam_studio.core.safety import sanitize
from types import SimpleNamespace
from .intent import decision_schema, read_decision, NoImplementation

class CheckpointStop(Exception):
    pass

class Worker:
    stop_exception = CheckpointStop

    def once(self, key, action):
        from .journal import perform
        return perform(self, key, action)

    def __init__(self, root: Path, token: str, protocol: int):
        self.store = Store(root)
        self.manifest = read_json(root / "manifest.json")
        self.token, self.protocol = token, protocol
        self.project = Path(self.manifest["project_root"])
        self.executable = self.manifest["git"]
        self.base = self.manifest["base_sha"]
        self.worktrees = {who: root / "worktrees" / who for who in ("a", "b", "result")}
        self.owners = {}
        self.ownership = {}
        self.tests_passed = False
        self.real = self.manifest.get("settings", {}).get("backend") == "real"
        self.label = "Codex" if self.real else "FAKE"
        self.context = {}
        self.codex = None
        self.homes = {}

    def accept(self):
        if self.protocol != 1 or self.manifest["schema_version"] != 1:
            raise ControllerError("protocol_mismatch", "GUI and worker must use protocol/schema version 1.")
        if self.token != self.manifest["token"]:
            raise ControllerError("handoff_invalid", "Worker handoff token did not match.")
        namespace = Path(self.manifest["namespace"])
        with guard(namespace / "index.lock"):
            path = namespace / "reservations" / f"{self.manifest['project_identity']}.json"
            reservation = read_json(path)
            if reservation["token"] != self.token or time.time() > reservation["handoff_deadline"] or reservation["accepted"]:
                raise ControllerError("handoff_invalid", "Reservation handoff expired or was already accepted.")
            reservation.update(worker=own_identity(), accepted=True)
            atomic(path, reservation)
            def accepted(state):
                state.update(accepted=True, worker=reservation["worker"])
                state["snapshot"].update(status="running", stage="preflight")
                self.store.event(state, "system", "status", f"App-owned {self.label} worker accepted protocol 1.", {"status": "running"})
            self.store.transaction(accepted)

    def controls(self, state):
        controls = state["details"]["controls"]
        for request in state["requests"]:
            if request["sequence"] <= controls["last_processed_sequence"]:
                continue
            kind = request["kind"]
            if kind in ("pause", "stop"):
                controls[kind].update(requested=True, acknowledged=True, acknowledged_at=now(), request_sequence=request["sequence"])
            elif kind == "resume" and not controls["stop"]["acknowledged"]:
                controls["pause"].update(requested=False, acknowledged=False, request_sequence=None, acknowledged_at=None)
            controls["last_processed_sequence"] = request["sequence"]
        return controls["stop"]["acknowledged"], controls["pause"]["acknowledged"]

    def checkpoint(self):
        while True:
            with guard(self.store.lock):
                state = self.store.read()
                before = state["details"]["controls"]["last_processed_sequence"]
                stopped, paused = self.controls(state)
                desired = "paused" if paused and not stopped else "running"
                changed = before != state["details"]["controls"]["last_processed_sequence"] or state["snapshot"]["status"] != desired
                state["snapshot"]["status"] = desired
                if changed:
                    self.store.event(state, "system", "status", "Stopped at checkpoint" if stopped else "Paused at checkpoint" if paused else "Running", {"status": "stopped" if stopped else desired})
                    self.store.publish(state)
            if stopped:
                raise CheckpointStop()
            if not paused:
                return
            time.sleep(.05)

    def stage(self, name):
        self.checkpoint()
        self.preserve_original()
        def change(state):
            state["snapshot"]["stage"] = name
            self.store.event(state, "system", "stage", name)
        self.store.transaction(change)

    def preserve_original(self):
        current = fingerprint(self.executable, self.project, Path(self.manifest["common_dir"]))
        if current != self.manifest["original"]:
            raise ControllerError("original_checkout_changed", "Original checkout or shared configuration changed. User files were preserved; inspect the evidence before continuing.")

    def child_record(self, record):
        self.store.transaction(lambda state: state["execution_processes"].append(record))

    def stage_prompt(self, who, stage, sha, scope):
        instructions = {
            "proposal": "Propose an independent architecture, risks, acceptance criteria and tests. Do not edit files.",
            "critique": "Critique the other agent's proposal with concrete tradeoffs. Do not edit files.",
            "plan": "Synthesize both proposals and critiques into one plan. Assign disjoint source and test paths to A and B. Each must implement meaningful source and tests. Do not edit files.",
            "challenge": "Challenge the synthesized plan, especially ownership conflicts and missing tests. Do not edit files.",
            "final_plan": "Finalize the shared plan addressing the challenge. Explicitly assign disjoint paths to A and B. Do not edit files.",
            "implementation": "Implement your assigned source and tests from the final shared plan in this worktree. Do not commit; the coordinator commits. Respect the other owner's paths.",
            "repair": "Fix blocking findings from the cross review in your assigned files and tests. Do not commit.",
            "integrated_repair": "Fix failing tests and blocking final-review findings in your assigned paths in the integrated worktree. Do not commit.",
        }
        prompt = f"Account {who.upper()}; stage {stage}.\nTask:\n{self.manifest['prompt']}\n"
        prompt += "Work only in the selected worktree. Never access credentials, push, deploy, purchase, change account settings, or modify the original checkout.\n"
        prompt += instructions.get(stage, "Inspect the diff and tests without changing files. Return only the review JSON matching the supplied schema.")
        if stage in {"proposal", "final_plan"}:
            prompt += ("\nReturn only JSON matching the supplied schema: decision and message. "
                       "Use decision=implement ONLY for a concrete, user-requested software change. "
                       "Use needs_input for an unclear task and no_work for greetings, status questions, "
                       "cancellation, or a plan that says to stop. Put the proposal or final plan in message. "
                       "Never invent work to satisfy the pipeline or contribution requirements. "
                       "An owner correction cancelling the task overrides the earlier plan.")
        if stage.endswith("_review"):
            prompt += f"\nreviewer={who}, scope={scope}, reviewed_sha={sha}, base_sha={self.base}, schema_version=1. Inspect git diff {self.base} {sha}. Report honest blocking findings; do not claim tests ran without evidence."
        if stage != "proposal":
            for key, value in self.context.items():
                prompt += f"\n--- {key} ---\n{value}"
        corrections = self.store.read()["details"]["controls"]["corrections"]
        if corrections:
            prompt += "\nOwner instructions, in order (remain in force throughout this run):\n"
            prompt += "\n".join(c["text"] for c in corrections)
        if len(prompt.encode("utf-8")) > 1024 * 1024:
            raise ControllerError("handoff_too_large", "Stage handoff exceeds the bounded prompt size; inspect retained transcripts.")
        return prompt

    def call(self, who, stage, folder, *, sha="", scope="cross", reject=False):
        return self.once(["call", who, stage, str(folder), sha, scope, reject],
                         lambda: self._call(who, stage, folder, sha=sha, scope=scope, reject=reject))

    def _call(self, who, stage, folder, *, sha="", scope="cross", reject=False):
        self.checkpoint()
        call_id = uuid.uuid4().hex
        prompt = self.stage_prompt(who, stage, sha, scope) if self.real else self.manifest["prompt"] + f"\nFAKE offline stage: {stage}. Account: {who}."
        def begin(state):
            nonlocal prompt
            stopped, paused = self.controls(state)
            if stopped: raise CheckpointStop()
            if paused: return False
            for correction in state["details"]["controls"]["corrections"]:
                if correction[who]["state"] == "pending":
                    prompt += "\nOwner correction: " + correction["text"]
                    correction[who].update(state="submitting", call_id=call_id, updated_at=now())
            state["details"]["active_calls"].append({"account": who, "call_id": call_id, "stage": stage,
                "started_at": now(), "deadline": (datetime.now(timezone.utc) + timedelta(seconds=self.manifest["max_call_seconds"])).isoformat(timespec="milliseconds").replace("+00:00", "Z"), "last_activity_at": None})
            state["details"]["usage"]["total_calls"] += 1
            self.store.event(state, who, "message", f"{self.label} {stage} invocation started", {"call_id": call_id})
            return True
        if not self.store.transaction(begin):
            return self._call(who, stage, folder, sha=sha, scope=scope, reject=reject)
        def delivery():
            def change(state):
                for correction in state["details"]["controls"]["corrections"]:
                    if correction[who]["state"] == "submitting" and correction[who]["call_id"] == call_id:
                        correction[who].update(state="delivered", updated_at=now())
                        self.store.event(state, who, "correction", "Correction prompt submission acknowledged", {"state": "delivered", "account": who, "call_id": call_id})
            self.store.transaction(change)
        normalized = []
        def line(channel, text):
            records = normalize(text, call_id, stage) if channel == "stdout" else []
            normalized.extend(records)
            if records:
                def change(state):
                    for kind, message, payload in records:
                        if kind == "usage": record_usage(state, who, payload)
                        self.store.event(state, who, kind, message, payload)
                    for call in state["details"]["active_calls"]:
                        if call["call_id"] == call_id: call["last_activity_at"] = now()
                self.store.transaction(change)
        if self.real:
            schema = None
            if stage in {"proposal", "final_plan"}:
                schema = self.store.root / f"{stage}.{who}.schema.json"
                atomic(schema, decision_schema())
            if stage.endswith("_review"):
                schema = self.store.root / "review.schema.json"
                atomic(schema, review_schema())
            args = invocation(self.codex, folder, stage in ("implementation", "repair", "integrated_repair"), schema)
        elif getattr(sys, "frozen", False):
            args = [sys.executable, "--fake-cli"]
        else:
            args = [sys.executable, str(Path(__file__).parent / "fake_cli.py")]
        if not self.real:
            args += ["--account", who, "--stage", stage, "--sha", sha, "--base", self.base, "--scope", scope]
            if reject: args.append("--reject")
        error = None
        try:
            code, out, err = execute(args, folder, self.manifest["max_call_seconds"], prompt=prompt,
                env=sanitized_environment(self.homes[who] if self.real else None), on_line=line, on_submitted=delivery, before_resume=self.child_record)
            transcript = "\n".join(message for kind, message, payload in normalized if kind == "message")
            self.store.transaction(lambda state: self.store.artifact(state, f"{self.label} {who.upper()} {stage} transcript", transcript + "\n" + err, "transcript"))
            if code or any(kind == "error" for kind, _, _ in normalized):
                raise ControllerError("agent_failed", f"{self.label} {who.upper()} {stage} failed (exit {code}). {sanitize(err[-1500:])}")
            if self.real and not transcript.strip():
                raise ControllerError("agent_response_missing", "Codex returned no surfaced response; inspect the retained transcript.")
            return normalized
        except BaseException as exc:
            error = exc
            if isinstance(exc, ControllerError):
                diagnostic = {"account": who, "stage": stage, "call_id": call_id,
                    "reason": exc.code, "deadline_seconds": self.manifest["max_call_seconds"],
                    "working_directory": str(folder), "command": sanitize(" ".join(args)),
                    "last_activity": self.store.read()["details"]["active_calls"],
                    "partial_stdout": getattr(exc, "stdout", ""), "partial_stderr": getattr(exc, "stderr", ""),
                    "recovery": "Review this call and retained files before retrying."}
                self.store.transaction(lambda state: self.store.artifact(state, f"{who.upper()} {stage} failure diagnostics", json.dumps(diagnostic, indent=2), "diagnostic"))
                if exc.code == "process_timeout":
                    raise ControllerError("process_timeout", f"Agent {who.upper()} timed out in {stage} after {self.manifest['max_call_seconds']} seconds. Partial output and call details are in failure diagnostics.") from exc
            raise
        finally:
            def ended(state):
                state["details"]["active_calls"] = [c for c in state["details"]["active_calls"] if c["call_id"] != call_id]
                if error:
                    for correction in state["details"]["controls"]["corrections"]:
                        if correction[who]["state"] == "submitting" and correction[who]["call_id"] == call_id:
                            correction[who].update(state="delivery_uncertain", updated_at=now())
            self.store.transaction(ended)

    def parallel(self, stage, folders=None):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.call, who, stage, (folders or self.worktrees)[who]) for who in ("a", "b")]
            # Wait for both contained calls before propagating an execution failure.
            results, errors = [], []
            for future in futures:
                try: results.append(future.result())
                except BaseException as exc: errors.append(exc)
            if errors:
                execution_errors = [e for e in errors if not isinstance(e, CheckpointStop)]
                raise (execution_errors or errors)[0]
            return results

    def commit(self, owner, message):
        return self.once(["commit", owner, message], lambda: self._commit(owner, message))

    def _commit(self, owner, message):
        root = self.worktrees[owner]
        self.stage_changes(root)
        if not git(self.executable, root, "diff", "--cached", "--name-only"):
            raise NoImplementation(f"Agent {owner.upper()} produced no file changes. The team stopped without an empty commit. Review the conversation before starting a new task.")
        git(self.executable, root, "commit", "-m", message)
        sha = git(self.executable, root, "rev-parse", "HEAD")
        scan_commits(self.executable, root, self.base, sha)
        return sha

    def stage_changes(self, root):
        if self.real:
            # Inspect filenames/links before staging or reading blobs. Never stage secrets.
            fingerprint(self.executable, root, Path(self.manifest["common_dir"]))
            git(self.executable, root, "add", "-A", "--", ".")
        else:
            # Stage only generated fixture source and test files. Python execution
            # can create __pycache__ files in tests/, which must never be committed:
            # their contents change on later test runs and invalidate pinned SHA evidence.
            git(self.executable, root, "add", "--", "autoteam_demo")
            for test_file in sorted((root / "tests").glob("test_autoteam_demo_*.py")):
                git(self.executable, root, "add", "--", str(test_file.relative_to(root)))

    def review(self, who, scope, folder, sha, reject=False):
        passed, context = self.once(["review", who, scope, str(folder), sha, reject],
                                   lambda: self._review(who, scope, folder, sha, reject))
        self.context[f"{scope}_review_{who}"] = context
        return passed

    def _review(self, who, scope, folder, sha, reject=False):
        before = git(self.executable, folder, "status", "--porcelain", "--untracked-files=all")
        output = self.call(who, scope + "_review", folder, sha=sha, scope=scope, reject=reject)
        messages = [message for kind, message, _ in output if kind == "message"]
        try:
            review = json.loads(messages[-1])
        except (ValueError, IndexError):
            raise ControllerError("review_invalid", "Structured review output is missing or malformed.")
        if before != git(self.executable, folder, "status", "--porcelain", "--untracked-files=all") or sha != git(self.executable, folder, "rev-parse", "HEAD"):
            raise ControllerError("review_mutation", "Review changed the inspected worktree or commit.")
        passed = validate_review(review, reviewer=who, scope=scope, reviewed_sha=sha, base_sha=self.base)
        self.context[f"{scope}_review_{who}"] = json.dumps(review)
        def save(state):
            aid = self.store.artifact(state, f"{self.label} {scope} review {who.upper()}", json.dumps(review), "review")
            state["details"]["gates"].append({"name": f"{scope}_review_{who}", "state": "passed" if passed else "failed", "sha": sha,
                "explanation": f"{self.label} review, bound to inspected SHA.", "artifact_ids": [aid]})
        self.store.transaction(save)
        return passed, json.dumps(review)

    def test_result(self, sha):
        passed, output = self.once(["tests", sha], lambda: self._test_result(sha))
        self.context["project_test_output"] = output
        return passed

    def _test_result(self, sha):
        authorization = self.manifest["authorization"]
        if not authorization["commands"]:
            raise ControllerError("tests_not_configured", "Unverified — no project tests configured.")
        if not authorization["authorized"] or authorization["project_identity"] != self.manifest["project_identity"]:
            raise ControllerError("tests_not_authorized", "Project test authorization does not match this run.")
        passed = True
        for args in authorization["commands"]:
            self.checkpoint()
            try:
                code, out, err = execute(args, self.worktrees["result"], authorization["timeout_seconds"],
                    env=sanitized_environment(), before_resume=self.child_record)
            except ControllerError as exc:
                diagnostic = {"command": args, "working_directory": str(self.worktrees["result"]),
                    "deadline_seconds": authorization["timeout_seconds"], "reason": exc.code,
                    "partial_stdout": getattr(exc, "stdout", ""), "partial_stderr": getattr(exc, "stderr", "")}
                self.store.transaction(lambda state: self.store.artifact(state, "Project test failure diagnostics", json.dumps(diagnostic, indent=2), "diagnostic"))
                raise
            def evidence(state):
                aid = self.store.artifact(state, "Authorized project test output", out + "\n" + err, "test_output")
                state["details"]["gates"].append({"name": "project_tests", "state": "passed" if code == 0 else "failed",
                    "sha": sha, "explanation": f"Authorized command exited {code}.", "artifact_ids": [aid]})
            self.store.transaction(evidence)
            passed = passed and code == 0
            self.context["project_test_output"] = sanitize(out + "\n" + err)[-24000:]
        return passed, self.context.get("project_test_output", "")

    def assert_pinned(self, sha):
        root = self.worktrees["result"]
        if git(self.executable, root, "rev-parse", "HEAD") != sha:
            raise ControllerError("result_mutated", "Result commit changed after evidence was collected.")
        # Only app-declared Python test caches are disposable.
        status = git(self.executable, root, "status", "--porcelain", "--untracked-files=all")
        unexpected = [line for line in status.splitlines() if not (line.startswith("?? ") and "__pycache__/" in line and line.endswith(".pyc"))]
        if unexpected:
            raise ControllerError("result_mutated", "Tests or reviews mutated deliverable files; SHA evidence is invalid.")
        scan_commits(self.executable, root, self.base, sha)

    def run(self):
        if self.real:
            settings = SimpleNamespace(**{key: Path(value) if key.endswith("_home") or key == "codex_executable" and value else value
                                         for key, value in self.manifest["settings"].items()})
            self.codex, self.homes = resolve_accounts(settings, self.project)
            for who in ("a", "b"):
                self.checkpoint()
                verify_login(self.codex, self.homes[who], self.project, self.child_record)
        self.preserve_original()
        _, _, identity, _ = inspect_project(self.executable, self.project)
        if identity != self.manifest["project_identity"]:
            raise ControllerError("identity_changed", "Repository identity changed before worktree creation.")
        self.stage("worktrees")
        for who, root in self.worktrees.items():
            root.parent.mkdir(parents=True, exist_ok=True)
            self.once(["worktree", who], lambda who=who, root=root: git(self.executable, self.project, "worktree", "add", "-b", f"autoteam/{self.manifest['run_id']}/{who}", str(root), self.base))
            self.store.transaction(lambda state, who=who, root=root: state["details"]["worktrees"].update({who: str(root)}))
        for stage in ("proposal", "critique", "plan", "challenge", "final_plan"):
            self.stage(stage)
            if self.real and stage in ("plan", "challenge", "final_plan"):
                who = "b" if stage == "challenge" else "a"
                results = [(who, self.call(who, stage, self.worktrees[who]))]
            else:
                results = list(zip(("a", "b"), self.parallel(stage)))
            for who, output in results:
                self.context[f"{stage}_{who}"] = "\n".join(message for kind, message, _ in output if kind == "message")
            if self.real and stage in {"proposal", "final_plan"}:
                for _, output in results:
                    read_decision(output)
        if not self.real and "[fake:crash]" in self.manifest["prompt"]:
            os._exit(71)
        self.stage("implementation")
        self.parallel("implementation")
        for who in ("a", "b"):
            sha = self.commit(who, f"{self.label} {who.upper()} source and tests")
            self.owners[who] = sha
            self.ownership[who] = list(filter(None, git(self.executable, self.worktrees[who], "diff", "--name-only", "-z", self.base, sha).split("\0")))
            evidence = substantive(self.executable, self.worktrees[who], self.base, sha)
            self.store.transaction(lambda state, who=who, evidence=evidence: self.store.artifact(state, f"Contribution {who.upper()}", json.dumps(evidence), "contribution"))
        verify_disjoint(self.ownership)
        self.stage("cross_review")
        for owner, reviewer in (("a", "b"), ("b", "a")):
            reject = not self.real and f"[fake:cross-repair-{owner}]" in self.manifest["prompt"]
            if not self.review(reviewer, "cross", self.worktrees[owner], self.owners[owner], reject):
                self.stage("repair")
                self.call(owner, "repair", self.worktrees[owner])
                verify_repair_paths(self.executable, self.worktrees[owner], self.ownership[owner])
                self.owners[owner] = self.commit(owner, f"{self.label} cross-review repair")
                if not self.review(reviewer, "cross", self.worktrees[owner], self.owners[owner]):
                    raise ControllerError("cross_review_rejected", "Owner did not receive fresh cross-review approval after repair.")
        self.stage("integration")
        for owner in ("a", "b"):
            commits = git(self.executable, self.worktrees[owner], "rev-list", "--reverse", f"{self.base}..{self.owners[owner]}").splitlines()
            for commit in commits:
                self.once(["integrate", commit], lambda commit=commit: git(self.executable, self.worktrees["result"], "cherry-pick", commit))
        for round_no in range(1, self.manifest["review_rounds"] + 1):
            sha = self.once(["review_round", round_no], lambda: git(self.executable, self.worktrees["result"], "rev-parse", "HEAD"))
            for owner in ("a", "b"):
                evidence = substantive(self.executable, self.worktrees["result"], self.base, self.owners[owner], sha, self.ownership[owner])
                self.store.transaction(lambda state, owner=owner, evidence=evidence: self.store.artifact(state, f"Retained contribution {owner.upper()} at {sha[:12]}", json.dumps(evidence), "contribution"))
            self.store.transaction(lambda state: state["details"].update(result_sha=sha, result_branch=f"autoteam/{self.manifest['run_id']}/result"))
            self.stage("project_tests")
            tests = self.test_result(sha)
            self.stage("final_review")
            reviews = [self.review(who, "final", self.worktrees["result"], sha,
                       reject=not self.real and round_no == 1 and "[fake:integrated-repair]" in self.manifest["prompt"]) for who in ("a", "b")]
            mutation = None
            try: self.assert_pinned(sha)
            except ControllerError as exc: mutation = exc
            if tests and all(reviews) and not mutation:
                self.tests_passed = True
                break
            if round_no == self.manifest["review_rounds"]:
                raise mutation or ControllerError("acceptance_failed", "Pinned result did not pass all authorized tests and both final reviews.")
            self.stage("integrated_repair")
            # Old evidence stays visible but cannot be accepted for a newer SHA.
            for who in ("a", "b"):
                self.owners[who] = self.once(["integrated_repair", who, sha], lambda who=who: self.integrated_repair(who, sha))
        self.preserve_original()
        self.finish("completed")

    def integrated_repair(self, who, sha):
        self.call(who, "integrated_repair", self.worktrees["result"], sha=sha)
        verify_repair_paths(self.executable, self.worktrees["result"], self.ownership[who])
        self.stage_changes(self.worktrees["result"])
        if git(self.executable, self.worktrees["result"], "diff", "--cached", "--name-only"):
            git(self.executable, self.worktrees["result"], "commit", "-m", f"{self.label} {who.upper()} integrated repair")
        return git(self.executable, self.worktrees["result"], "rev-parse", "HEAD")

    def finish(self, status, error=None, no_work=None):
        # Only an orderly stop with no unresolved operation can be restarted.
        # Content/config fingerprints prevent stale cached calls from hiding edits.
        resume_point = None
        current = self.store.read()
        if no_work is None and (status == "stopped" or (not error and current["details"]["controls"]["stop"]["requested"])) and not current.get("journal", {}).get("inflight"):
            resume_point = {who: fingerprint(self.executable, root, Path(self.manifest["common_dir"]))
                            for who, root in self.worktrees.items() if root.exists()}
        with guard(self.store.lock):
            state = self.store.read()
            if state["snapshot"]["status"] in TERMINAL:
                return
            stopped, _ = self.controls(state)
            if error:
                status = "failed"  # Observed execution failure takes priority over Stop.
            elif stopped:
                status = "stopped"
            if status == "completed":
                if not self.tests_passed:
                    raise ControllerError("acceptance_failed", "No verified project tests; completion is prohibited.")
                self.assert_pinned(state["details"]["result_sha"])
            reason = "task_needs_input" if no_work else error.code if error else "operator_stop" if status == "stopped" else None
            report = f"Backend: {'Codex real' if self.real else 'FAKE offline fixture'}\nStatus: {status}\nResult SHA: {state['details']['result_sha']}\nReason: {reason}\n"
            if error: report += error.message + "\n"
            if no_work: report += str(no_work) + "\n"
            report += "\nProject: " + str(self.project) + "\n"
            report += "\n".join(f"{who.upper()} worktree: {root}" for who, root in self.worktrees.items()) + "\n"
            report += "\nVerification evidence:\n" + "\n".join(f"{g['name']}: {g['state']} at {g['sha']} — {g['explanation']}" for g in state["details"]["gates"]) + "\n"
            report += "\nPending messages:\n" + "\n".join(f"{c['text']} | A: {c['a']['state']} | B: {c['b']['state']}" for c in state["details"]["controls"]["corrections"] if any(c[w]["state"] != "delivered" for w in ("a", "b"))) + "\n"
            report += "\nResume: " + ("Verified orderly checkpoint available." if resume_point is not None else "Automatic resume unavailable; inspect retained worktrees for interrupted/failed runs.") + "\n"
            aid = self.store.artifact(state, "Final report", report, "report")
            state["snapshot"].update(status=status, stage="awaiting_task" if no_work else status, error=error.message if error else str(no_work) if no_work else None,
                                      result_dir=state["details"]["worktrees"]["result"])
            state["details"].update(reason_code=reason, active_calls=[])
            state["resume_point"] = resume_point
            self.store.event(state, "system", "artifact", "Final report published", {"artifact_id": aid})
            self.store.event(state, "system", "status", f"Run {status}", {"status": status, "reason": reason})
            self.store.publish(state)
        namespace = Path(self.manifest["namespace"])
        with guard(namespace / "index.lock"):
            path = namespace / "reservations" / f"{self.manifest['project_identity']}.json"
            if path.exists() and read_json(path)["token"] == self.token:
                path.unlink()

    def execute(self):
        # Invalid handoffs must never write state or release another owner's
        # reservation. The launching facade records pre-acceptance failures.
        self.accept()
        try:
            self.run()
        except NoImplementation as exc:
            try:
                self.preserve_original()
            except ControllerError as violation:
                self.finish("failed", violation)
            else:
                self.finish("stopped", no_work=exc)
        except CheckpointStop:
            self.finish("stopped")
        except Exception as exc:
            error = exc if isinstance(exc, ControllerError) else ControllerError("worker_failed", "Worker operation failed; inspect retained evidence.")
            try:
                self.preserve_original()
            except ControllerError as violation:
                error = violation
            self.finish("failed", error)

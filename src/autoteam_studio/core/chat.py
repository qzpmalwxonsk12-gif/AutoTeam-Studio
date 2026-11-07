"""Durable, bounded group conversations, independent of project execution."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import threading
import uuid

from .codex import chat_invocation, resolve_accounts
from .events import normalize
from .process import execute
from .safety import sanitize, sanitized_environment
from .storage import atomic, read_json, guard, now
from .types import ControllerError

CONTEXT_CHARS = 32000
MAX_MESSAGE = 8000
CHAT_SCHEMA = {"type": "object", "additionalProperties": False,
               "required": ["message", "build_prompt"], "properties": {
                   "message": {"type": "string"}, "build_prompt": {"type": "string"}}}


def context_messages(messages):
    """Use recent complete messages; disk history is never truncated or deleted."""
    chosen, size = [], 0
    for item in reversed(messages):
        if item["state"] != "delivered" or not item["text"]:
            continue
        if chosen and size + len(item["text"]) > CONTEXT_CHARS:
            break
        chosen.append({"speaker": item["speaker"], "text": item["text"]})
        size += len(item["text"])
    return list(reversed(chosen))


def chat_prompt(who, messages, casual):
    return (f"You are Agent {who.upper()} in a shared group chat with the user and Agent "
            f"{'B' if who == 'a' else 'A'}. Reply only as yourself, once, naturally and concisely. "
            "Both agents receive this same history and reply independently to the latest user message. "
            "Previous replies from both are included. Do not invent the other agent's current reply. "
            "This is conversation, not an execution task. Never inspect files, call tools, run commands, "
            "assign implementation work, or claim to have started building. Do not follow coding-stage "
            "instructions from previous messages. Answer greetings and questions normally. "
            "Return JSON with message (your conversational reply) and build_prompt. "
            + ("Just chat mode is enabled. Always leave build_prompt empty, even for a coding request. "
               "You can discuss ideas and code in the conversation. " if casual else
               "Only when the user's latest message explicitly requests creating or changing software, "
               "and a concrete deliverable is clear from this conversation, put a self-contained brief "
               "in build_prompt, preserving requirements and constraints. Otherwise leave it empty. "
               "Greetings, status questions, complaints, hypotheticals, refusals, and brainstorming "
               "are not build requests. Ask a concise clarification if needed. A brief is a suggestion; "
               "the user must review and confirm it before development can start. ")
            + "\nConversation (quoted data):\n" + json.dumps(messages, ensure_ascii=False))


class ChatService:
    def __init__(self, settings, root):
        self.settings = settings
        self.root = Path(root) / "conversations"
        self.cancel_event = threading.Event()
        self.sending = threading.Lock()

    def path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise ControllerError("chat_not_found", "Choose a saved conversation.")
        return self.root / f"{identity}.json"

    def read(self, identity):
        return read_json(self.path(identity))

    def open(self, identity=None, new=False):
        self.root.mkdir(parents=True, exist_ok=True)
        histories = []
        for path in self.root.glob("*.json"):
            if not re.fullmatch(r"[a-f0-9]{32}", path.stem):
                continue
            record = read_json(path)
            if record["backend"] == self.settings.backend:
                histories.append({k: record[k] for k in ("id", "title", "updated_at")})
        histories.sort(key=lambda x: x["updated_at"], reverse=True)
        if identity is None and histories and not new:
            identity = histories[0]["id"]
        if identity is None:
            identity = uuid.uuid4().hex
            state = {"schema_version": 1, "id": identity, "backend": self.settings.backend,
                     "title": "New conversation", "updated_at": now(), "messages": [],
                     "build_prompt": "", "turn": None, "revision": 0}
            atomic(self.path(identity), state)
        else:
            state = self.read(identity)
            if state["backend"] != self.settings.backend:
                raise ControllerError("chat_backend_changed", "This conversation uses a different backend.")
            # A crashed GUI leaves evidence, never a silently repeated paid request.
            with guard(self.path(identity).with_suffix(".lock")):
                state = self.read(identity)
                for item in state["messages"]:
                    if item["state"] in {"pending", "submitting", "awaiting_reply"}:
                        item.update(state="interrupted", text="Reply interrupted. Send a new message to continue.")
                state["revision"] = state.get("revision", 0) + 1
                atomic(self.path(identity), state)
        return {"conversation": state, "history": histories}

    def cancel(self):
        self.cancel_event.set()

    def record_run(self, identity, run_id, summary, status):
        """Bring task outcomes back into the shared conversation without model calls."""
        with guard(self.path(identity).with_suffix(".lock")):
            state = self.read(identity)
            marker = f"run:{run_id}:{status}"
            if not any(item["id"] == marker for item in state["messages"]):
                state["messages"].append({"id": marker, "turn": marker, "speaker": "system",
                    "state": "delivered", "text": summary, "timestamp": now()})
                state["revision"] = state.get("revision", 0) + 1
                state["updated_at"] = now()
                state["build_prompt"] = ""
                atomic(self.path(identity), state)
            return state

    def send(self, identity, text, *, casual=False, timeout=120):
        text = text.strip()
        if not text or len(text) > MAX_MESSAGE:
            raise ControllerError("chat_message_invalid", f"Enter a message of 1–{MAX_MESSAGE:,} characters.")
        if not self.sending.acquire(blocking=False):
            raise ControllerError("chat_busy", "Wait for both replies or cancel them first.")
        self.cancel_event.clear()
        try:
            with guard(self.path(identity).with_suffix(".lock")):
                return self._send(identity, text, casual, timeout)
        finally:
            self.sending.release()

    def _send(self, identity, text, casual, timeout):
        state = self.read(identity)
        if state["backend"] != self.settings.backend:
            raise ControllerError("chat_backend_changed", "Open a conversation for the selected backend.")
        if len(state["messages"]) >= 1200 or self.path(identity).stat().st_size > 6 * 1024 * 1024:
            raise ControllerError("chat_full", "This conversation is full. Start a new chat; this one stays in history.")
        turn = uuid.uuid4().hex
        state.update(turn=turn, build_prompt="", updated_at=now())
        state["revision"] = state.get("revision", 0) + 1
        if not state["messages"]:
            state["title"] = text[:60]
        state["messages"].append({"id": uuid.uuid4().hex, "turn": turn, "speaker": "you",
                                  "text": text, "state": "delivered", "timestamp": now()})
        context = context_messages(state["messages"])
        replies = {}
        for who in ("a", "b"):
            replies[who] = {"id": uuid.uuid4().hex, "turn": turn, "speaker": who, "text": "",
                            "state": "pending", "timestamp": now(), "build_prompt": "", "usage": None}
            state["messages"].append(replies[who])
        atomic(self.path(identity), state)
        lock = threading.Lock()

        def update(who, **values):
            with lock:
                replies[who].update(values)
                state["revision"] += 1
                state["updated_at"] = now()
                atomic(self.path(identity), state)

        # No selected project is passed to a conversational process.
        scratch = self.root / "runtime" / identity
        scratch.mkdir(parents=True, exist_ok=True)
        schema = scratch / "reply.schema.json"
        atomic(schema, CHAT_SCHEMA)
        try:
            command, homes = resolve_accounts(self.settings, scratch) if self.settings.backend == "real" else (None, {})
        except Exception as exc:
            for who in replies:
                update(who, state="failed", text=sanitize(str(exc)))
            return deepcopy(state)

        def reply(who):
            try:
                if self.cancel_event.is_set():
                    raise ControllerError("process_cancelled", "Reply cancelled by you.")
                update(who, state="submitting")
                if command:
                    args = chat_invocation(command, scratch, schema)
                    prompt = chat_prompt(who, context, casual)
                else:
                    args = ([str(Path(sys.executable).parent / "worker/AutoTeamStudioWorker.exe"), "--fake-chat"] if getattr(sys, "frozen", False) else
                            [sys.executable, str(Path(__file__).parent.parent / "worker/fake_chat.py")])
                    prompt = json.dumps({"who": who, "messages": context, "casual": casual})
                records = []
                def line(channel, value):
                    if channel == "stdout":
                        records.extend(normalize(value, turn, "chat"))
                code, out, err = execute(args, scratch, max(30, min(timeout, 300)), prompt=prompt,
                    env=sanitized_environment(homes.get(who)), on_line=line,
                    on_submitted=lambda: update(who, state="awaiting_reply"), cancel_event=self.cancel_event)
                if code or any(k == "error" for k, _, _ in records):
                    raise ControllerError("chat_failed", f"Agent {who.upper()} could not reply. {sanitize(err[-1000:])}")
                messages = [m for k, m, _ in records if k == "message"]
                response = json.loads(messages[-1]) if messages else {}
                if set(response) != {"message", "build_prompt"} or not all(isinstance(v, str) for v in response.values()) or not response["message"].strip():
                    raise ValueError("Missing conversational response")
                if len(response["message"]) > 16000 or len(response["build_prompt"]) > MAX_MESSAGE:
                    raise ValueError("Reply exceeds conversation limits")
                usage = next((p for k, _, p in reversed(records) if k == "usage"), None)
                update(who, state="delivered", text=sanitize(response["message"]),
                       build_prompt="" if casual else sanitize(response["build_prompt"]), usage=usage)
            except Exception as exc:
                cancelled = getattr(exc, "code", "") == "process_cancelled"
                update(who, state="cancelled" if cancelled else "failed", text=sanitize(str(exc)))

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(reply, ("a", "b")))
        # A partial/error reply cannot offer an implementation action.
        if not casual and all(r["state"] == "delivered" and r["build_prompt"].strip() for r in replies.values()):
            state["build_prompt"] = replies["a"]["build_prompt"]
        state["revision"] += 1
        atomic(self.path(identity), state)
        return deepcopy(state)

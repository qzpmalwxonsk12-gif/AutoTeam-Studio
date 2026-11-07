"""Allowlisted Codex JSONL normalization and per-call cumulative usage."""
from __future__ import annotations

import json
from .safety import sanitize

def normalize(line: str, call_id: str, stage: str):
    if len(line.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Raw activity exceeds 1 MiB")
    try:
        record = json.loads(line)
    except ValueError:
        return [("error", "Malformed CLI activity skipped.", {"code": "malformed_cli", "call_id": call_id})]
    if not isinstance(record, dict):
        return []
    kind = record.get("type")
    common = {"schema_version": 1, "stage": stage, "call_id": call_id}
    if kind == "turn.completed":
        usage = record.get("usage", {})
        if not isinstance(usage, dict): return []
        counts = {k: usage.get(k) for k in ("input_tokens", "output_tokens", "cached_input_tokens")}
        if any(type(counts[k]) is not int or counts[k] < 0 for k in ("input_tokens", "output_tokens")):
            return []
        if counts["cached_input_tokens"] is not None and (type(counts["cached_input_tokens"]) is not int or counts["cached_input_tokens"] < 0):
            return []
        sequence = record.get("report_sequence", 0)
        if type(sequence) is not int or sequence < 0: return []
        return [("usage", "Reported call usage", {**common, **counts, "report_sequence": sequence})]
    if kind in ("error", "turn.failed"):
        error = record.get("error", record)
        detail = error.get("message", "CLI reported an execution error.") if isinstance(error, dict) else str(error)
        return [("error", sanitize(detail)[:4000], {**common, "code": "cli_error"})]
    if kind not in ("item.started", "item.updated", "item.completed"):
        return []
    item = record.get("item", {})
    if not isinstance(item, dict): return []
    item_type = item.get("type")
    if item_type == "agent_message" and isinstance(item.get("text"), str):
        return [("message", sanitize(item["text"]), common)]
    if item_type == "command_execution":
        command = item.get("command", "")
        if not isinstance(command, str): command = "Command activity"
        code = item.get("exit_code")
        if code is not None and type(code) is not int: code = None
        state = item.get("status")
        if state not in ("in_progress", "completed", "failed"): state = "unknown"
        output = item.get("aggregated_output", "")
        return [("command", sanitize(command), {**common, "state": state, "exit_code": code,
                "output": sanitize(output)[-4000:] if isinstance(output, str) else ""})]
    if item_type == "file_change":
        changes = item.get("changes", [])
        if not isinstance(changes, list): return []
        paths = [sanitize(c["path"]) for c in changes[:100] if isinstance(c, dict) and isinstance(c.get("path"), str)]
        actions = [f"{c.get('kind', 'changed')}: {c['path']}" for c in changes[:100] if isinstance(c, dict) and isinstance(c.get("path"), str)]
        return [("file_change", sanitize("\n".join(actions)) or "Changed files", {**common, "paths": paths})]
    # Reasoning, environment, tool outputs and unknown types are never persisted.
    return []

def record_usage(state, account: str, payload: dict):
    call = payload["call_id"]
    sequence = payload["report_sequence"]
    previous = state["usage_calls"].get(call)
    if previous and sequence <= previous["sequence"]:
        return
    state["usage_calls"][call] = {"account": account, "sequence": sequence,
                                  "input": payload["input_tokens"], "output": payload["output_tokens"], "cached": payload["cached_input_tokens"]}
    records = list(state["usage_calls"].values())
    state["details"]["usage"]["known_calls"] = len(records)
    for who in ("a", "b"):
        reported = [r for r in records if r["account"] == who]
        state["snapshot"][f"reported_tokens_{who}"] = sum(r["input"] + r["output"] for r in reported) if reported else None
        cached = [r["cached"] for r in reported if r["cached"] is not None]
        state["details"]["usage"][f"cached_input_{who}"] = sum(cached) if cached else None

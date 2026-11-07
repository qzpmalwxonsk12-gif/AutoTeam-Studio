from dataclasses import asdict
import json
import threading

import pytest

from autoteam_studio.core.api import initial_state, ControllerError
from autoteam_studio.core.events import normalize, record_usage
from autoteam_studio.core.storage import Store, read_events, encode, atomic, read_json, guard, io_path
from autoteam_studio.core.safety import sanitize, contained_path

def create_store(tmp_path):
    store = Store(tmp_path / "run")
    store.root.mkdir()
    state = initial_state("example", tmp_path, "identity", "fake")
    store.publish(state)
    return store

def test_raw_allowlist_ignores_reasoning_and_secrets():
    assert normalize(json.dumps({"type": "item.completed", "item": {"type": "reasoning", "text": "never retain"}}), "call", "proposal") == []
    assert normalize(json.dumps({"type": "unrecognized", "environment": {"API_KEY": "never retain"}}), "call", "proposal") == []
    event = normalize(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Authorization: Bearer never-retain"}}), "call", "proposal")[0]
    assert "never-retain" not in event[1]
    assert normalize("not json", "call", "proposal")[0][0] == "error"
    with pytest.raises(ValueError): normalize("x" * (1024 * 1024 + 1), "call", "stage")

def test_cumulative_usage_replaces_per_call_deduplicates_and_excludes_cache(tmp_path):
    state = initial_state("example", tmp_path, "identity", "fake")
    def report(call, sequence, input_count, output_count):
        return {"call_id": call, "report_sequence": sequence, "input_tokens": input_count,
                "output_tokens": output_count, "cached_input_tokens": 4}
    record_usage(state, "a", report("first", 1, 10, 5))
    record_usage(state, "a", report("first", 1, 999, 999))
    record_usage(state, "a", report("first", 2, 20, 7))
    record_usage(state, "a", report("second", 1, 3, 2))
    assert state["snapshot"]["reported_tokens_a"] == 32
    assert state["details"]["usage"]["cached_input_a"] == 8
    assert state["snapshot"]["reported_tokens_b"] is None
    assert state["details"]["usage"]["known_calls"] == 2

def test_immutable_generation_high_water_and_cursors(tmp_path):
    store = create_store(tmp_path)
    old = store.read()
    store.transaction(lambda s: store.event(s, "a", "message", "first"))
    published = store.read()
    store.transaction(lambda s: store.event(s, "b", "message", "second"))
    events, cursor, more = read_events(store.root, "example", 0, published["high_water"])
    assert [e.message for e in events] == ["first"] and not more
    assert old["high_water"] == 0
    for invalid in (-1, True, 1, published["high_water"] + 1):
        with pytest.raises(ValueError): read_events(store.root, "example", invalid, published["high_water"])
    assert cursor == published["high_water"]

def test_batch_limits_and_oversized_sanitized_artifact(tmp_path):
    store = create_store(tmp_path)
    def populate(state):
        for i in range(205):
            store.event(state, "a", "message", f"record {i}")
        store.event(state, "b", "message", "x" * 140000)
    store.transaction(populate)
    state = store.read()
    first, cursor, more = read_events(store.root, "example", 0, state["high_water"])
    assert len(first) == 200 and more
    second, end, more = read_events(store.root, "example", cursor, state["high_water"])
    assert len(second) == 6 and not more
    assert second[-1].payload["artifact_id"] == state["artifacts"][0]["id"]
    assert all(len(encode(asdict(e))) <= 128 * 1024 for e in first + second)
    assert end == state["high_water"]

def test_malformed_complete_records_consumed_partial_tail_unread(tmp_path):
    store = create_store(tmp_path)
    data = b"bad record\n" + encode({"run_id": "example", "agent": "a", "timestamp": "2026-10-10T08:13:40.000Z", "kind": "message", "message": "good", "payload": {}}) + b'{"unfinished":'
    (store.root / "events.jsonl").write_bytes(data)
    events, offset, more = read_events(store.root, "example", 0, len(data))
    assert [e.kind for e in events] == ["error", "message"]
    assert offset == len(data) - len(b'{"unfinished":') and more

def test_persisted_payload_is_allowlisted_and_sanitized_on_read(tmp_path):
    store = create_store(tmp_path)
    data = encode({"run_id": "example", "agent": "a", "timestamp": "2026-10-10T08:13:40.000Z",
        "kind": "message", "message": "Visible activity", "payload": {
            "environment": {"PRIVATE": "never-show"}, "unknown": "never-show",
            "paths": ["Authorization: Bearer never-show"], "call_id": "call"}})
    (store.root / "events.jsonl").write_bytes(data)
    events, cursor, more = read_events(store.root, "example", 0, len(data))
    assert cursor == len(data) and not more
    assert set(events[0].payload) == {"paths", "call_id"}
    assert "never-show" not in json.dumps(asdict(events[0]))

def test_missing_or_truncated_published_event_log_fails_explicitly(tmp_path):
    store = create_store(tmp_path)
    with pytest.raises(ControllerError) as error:
        read_events(store.root, "example", 0, 10)
    assert error.value.code == "storage_corrupt"
    (store.root / "events.jsonl").write_bytes(b"short\n")
    with pytest.raises(ControllerError) as error:
        read_events(store.root, "example", 0, 10)
    assert error.value.code == "storage_corrupt"

def test_persistence_failure_does_not_replace_current_generation(tmp_path, monkeypatch):
    import autoteam_studio.core.storage as storage
    store = create_store(tmp_path)
    generation = store.read()["generation"]
    real_replace = storage.os.replace
    def failing(source, target):
        if str(target).endswith("current.json"):
            raise OSError("fixture disk full")
        return real_replace(source, target)
    monkeypatch.setattr(storage.os, "replace", failing)
    with pytest.raises(ControllerError) as error:
        store.transaction(lambda s: s["snapshot"].update(status="completed"))
    assert error.value.code == "persistence_failure"
    assert store.read()["generation"] == generation
    assert store.read()["snapshot"]["status"] == "queued"

def test_retention_overflow_is_explicit(tmp_path, monkeypatch):
    import autoteam_studio.core.storage as storage
    store = create_store(tmp_path)
    monkeypatch.setattr(storage, "RETENTION_LIMIT", 1500)
    with pytest.raises(ControllerError) as error:
        store.transaction(lambda state: store.artifact(state, "Large", "x" * 10000))
    assert error.value.code == "retention_limit"

def test_changed_artifact_target_refused(tmp_path):
    evidence = tmp_path / "evidence"; evidence.mkdir()
    safe = evidence / "safe.txt"; safe.write_text("content")
    assert contained_path(evidence, safe) == safe
    outside = tmp_path / "outside.txt"; outside.write_text("content")
    with pytest.raises(ControllerError): contained_path(evidence, outside)

def test_replaced_evidence_junction_refused_even_if_artifact_follows_it(tmp_path, monkeypatch):
    from pathlib import Path
    evidence = tmp_path / "evidence"; evidence.mkdir()
    safe = evidence / "safe.txt"; safe.write_text("content")
    replacement = tmp_path / "replacement"; replacement.mkdir()
    target = replacement / "safe.txt"; target.write_text("changed")
    resolve = Path.resolve
    def changed(path, *args, **kwargs):
        if path == evidence: return replacement
        if path == safe: return target
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", changed)
    with pytest.raises(ControllerError) as error: contained_path(evidence, safe)
    assert error.value.code == "artifact_unsafe"

def test_guard_has_deadline_and_mutations_serialize(tmp_path):
    lock = tmp_path / "guard.lock"
    acquired = threading.Event(); release = threading.Event()
    def hold():
        with guard(lock):
            acquired.set(); release.wait(2)
    thread = threading.Thread(target=hold); thread.start(); acquired.wait(2)
    try:
        with pytest.raises(ControllerError) as error:
            with guard(lock, timeout=.02): pass
        assert error.value.code == "operation_timeout"
    finally:
        release.set(); thread.join()

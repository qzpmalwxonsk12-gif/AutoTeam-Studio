"""Durable completed operations. In-flight writes are never guessed or replayed."""
import hashlib
import json

from autoteam_studio.core.storage import atomic, read_json, encode
from autoteam_studio.core.types import ControllerError


def perform(worker, key, action):
    step_id = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()
    journal = worker.store.read().get("journal", {})
    path = worker.store.root / "steps" / f"{step_id}.json"
    if step_id in journal.get("done", {}):
        saved = read_json(path)
        if hashlib.sha256(encode(saved)).hexdigest() != journal["done"][step_id]:
            raise ControllerError("checkpoint_changed", "A saved completed step changed; automatic resume is blocked.")
        return saved["result"]

    worker.checkpoint()
    def begun(state):
        state.setdefault("journal", {"done": {}, "inflight": {}})["inflight"][step_id] = key
    worker.store.transaction(begun)
    try:
        result = action()
        saved = {"key": key, "result": result}
        atomic(path, saved)
        def completed(state):
            state["journal"]["inflight"].pop(step_id, None)
            state["journal"]["done"][step_id] = hashlib.sha256(encode(saved)).hexdigest()
        worker.store.transaction(completed)
        return result
    except worker.stop_exception:
        worker.store.transaction(lambda state: state["journal"]["inflight"].pop(step_id, None))
        raise

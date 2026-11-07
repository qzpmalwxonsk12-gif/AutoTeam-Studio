"""Structured exits: conversation is never an implementation plan."""
import json
from autoteam_studio.core.types import ControllerError


class NoImplementation(Exception):
    pass


def decision_schema():
    return {"type": "object", "additionalProperties": False,
            "required": ["decision", "message"], "properties": {
                "decision": {"type": "string", "enum": ["implement", "needs_input", "no_work"]},
                "message": {"type": "string"}}}


def read_decision(records):
    try:
        text = [message for kind, message, _ in records if kind == "message"][-1]
        value = json.loads(text)
        if (set(value) != {"decision", "message"} or
            value["decision"] not in {"implement", "needs_input", "no_work"} or
            not isinstance(value["message"], str) or not value["message"].strip()):
            raise ValueError()
    except (IndexError, ValueError, TypeError):
        raise ControllerError("task_decision_invalid", "The agent did not provide a valid task decision. Implementation was not started.") from None
    if value["decision"] != "implement":
        raise NoImplementation(value["message"])
    return value

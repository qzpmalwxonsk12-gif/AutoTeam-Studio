"""Offline conversational worker. Never invokes Codex or reads credentials."""
import json
import re
import sys
import time


def main():
    request = json.loads(sys.stdin.read())
    text = request["messages"][-1]["text"]
    if "[fake:slow]" in text:
        time.sleep(3)
    if request["who"] == "b" and "[fake:fail-b]" in text:
        return 1
    build = bool(re.match(r"(?i)(?:please )?(?:build|create|implement|fix)\b", text)) and not request["casual"]
    message = ("I can help with that. Review the project brief when you are ready." if build else
               f"Hello from Agent {request['who'].upper()}! This is an offline chat reply. You said: {text}")
    response = {"message": message, "build_prompt": text if build else ""}
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(response)}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline subprocess fixture. Executed through actual stdin/stdout/stderr pipes.

This is deliberately labeled fake and never imports or launches Codex.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

def emit(item):
    print(json.dumps(item), flush=True)

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true")
    parser.add_argument("--account", choices=("a", "b"))
    parser.add_argument("--stage")
    parser.add_argument("--sha", default="")
    parser.add_argument("--base", default="")
    parser.add_argument("--scope", default="cross")
    parser.add_argument("--reject", action="store_true")
    args = parser.parse_args(argv)
    if args.test:
        tests = unittest.defaultTestLoader.discover("tests", pattern="test_autoteam_demo_*.py")
        if tests.countTestCases() < 2:
            print("Fake demo requires both owners' tests", file=sys.stderr)
            return 2
        return 0 if unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(tests).wasSuccessful() else 1
    prompt = sys.stdin.read()
    emit({"type": "item.completed", "item": {"type": "agent_message", "text": f"FAKE account {args.account.upper()}: {args.stage}; received {len(prompt)} prompt characters."}})
    print("FAKE CLI diagnostic: offline fixture", file=sys.stderr, flush=True)
    time.sleep(.12)
    if args.stage in ("implementation", "repair", "integrated_repair"):
        namespace = Path("autoteam_demo"); namespace.mkdir(exist_ok=True)
        tests = Path("tests"); tests.mkdir(exist_ok=True)
        value = int(hashlib.sha256(prompt.encode()).hexdigest()[:4], 16)
        source = namespace / f"{args.account}.py"
        source.write_text(f'"""App-owned offline demo contribution {args.account.upper()}."""\ndef transform(value):\n    return value * 2 + {value}\n', encoding="utf-8")
        test = tests / f"test_autoteam_demo_{args.account}.py"
        test.write_text(f'import runpy\nfrom pathlib import Path\nimport unittest\n\nclass Demo{args.account.upper()}Test(unittest.TestCase):\n    def test_transform(self):\n        function = runpy.run_path(str(Path(__file__).resolve().parents[1] / "autoteam_demo/{args.account}.py"))["transform"]\n        self.assertEqual(function(3), {value + 6})\n        self.assertEqual(function(-2), {value - 4})\n', encoding="utf-8")
        emit({"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": str(source)}, {"path": str(test)}]}})
    if args.stage in ("cross_review", "final_review"):
        findings = []
        if args.reject:
            findings = [{"id": "demo-repair", "severity": "medium", "path": None, "line": None,
                         "message": "FAKE fixture requests a repair", "required_change": "Refresh the owner's source and tests"}]
        review = {"schema_version": 1, "reviewer": args.account, "scope": args.scope, "reviewed_sha": args.sha,
                  "base_sha": args.base, "passed": not args.reject, "summary": "FAKE structured review fixture; not model verification.",
                  "blocking_findings": findings, "nonblocking_findings": [], "evidence_artifact_ids": []}
        emit({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(review)}})
    emit({"type": "turn.completed", "report_sequence": 1, "usage": {"input_tokens": 11, "output_tokens": 7, "cached_input_tokens": 3}})
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

from autoteam_studio.core.project import git
from autoteam_studio.core.types import ControllerError

def review_schema():
    finding = {"type": "object", "additionalProperties": False,
        "properties": {"id": {"type": "string"}, "severity": {"enum": ["critical", "high", "medium", "low"]},
            "path": {"type": ["string", "null"]}, "line": {"type": ["integer", "null"]},
            "message": {"type": "string"}, "required_change": {"type": "string"}}}
    finding["required"] = list(finding["properties"])
    properties = {"schema_version": {"type": "integer", "enum": [1]},
        "reviewer": {"enum": ["a", "b"]}, "scope": {"enum": ["cross", "final"]},
        "reviewed_sha": {"type": "string"}, "base_sha": {"type": "string"},
        "passed": {"type": "boolean"}, "summary": {"type": "string"},
        "blocking_findings": {"type": "array", "items": finding},
        "nonblocking_findings": {"type": "array", "items": finding},
        "evidence_artifact_ids": {"type": "array", "items": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties)}

def validate_review(review, *, reviewer, scope, reviewed_sha, base_sha):
    keys = {"schema_version", "reviewer", "scope", "reviewed_sha", "base_sha", "passed", "summary", "blocking_findings", "nonblocking_findings", "evidence_artifact_ids"}
    if not isinstance(review, dict) or set(review) != keys or type(review["schema_version"]) is not int or review["schema_version"] != 1 or review["reviewer"] != reviewer or review["scope"] != scope or review["reviewed_sha"] != reviewed_sha or review["base_sha"] != base_sha or type(review["passed"]) is not bool or not isinstance(review["summary"], str):
        raise ControllerError("review_invalid", "Review output is malformed or is bound to the wrong commit.")
    if not isinstance(review["evidence_artifact_ids"], list) or any(not isinstance(x, str) for x in review["evidence_artifact_ids"]):
        raise ControllerError("review_invalid", "Review artifact references are invalid.")
    rejected = not review["passed"]
    for label in ("blocking_findings", "nonblocking_findings"):
        if not isinstance(review[label], list):
            raise ControllerError("review_invalid", "Review findings must be lists.")
        for finding in review[label]:
            if not isinstance(finding, dict) or set(finding) != {"id", "severity", "path", "line", "message", "required_change"}:
                raise ControllerError("review_invalid", "Review finding fields are invalid.")
            if finding["severity"] not in ("critical", "high", "medium", "low") or any(not isinstance(finding[k], str) for k in ("id", "message", "required_change")) or (finding["path"] is not None and not isinstance(finding["path"], str)) or (finding["line"] is not None and (type(finding["line"]) is not int or finding["line"] < 1)):
                raise ControllerError("review_invalid", "Review finding values are invalid.")
            if label == "blocking_findings" or finding["severity"] in ("critical", "high"):
                rejected = True
    return not rejected

SOURCE_EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".kts",
    ".cs", ".go", ".rs", ".c", ".cpp", ".cc", ".h", ".hpp", ".rb", ".php", ".swift",
    ".dart", ".vue", ".svelte", ".html", ".css", ".scss", ".sql", ".sh", ".ps1", ".r", ".scala", ".lua"}


def code_signature(text, suffix):
    """Structural evidence only. Tests and peer reviews still establish acceptance."""
    if suffix == ".py":
        tree = ast.parse(text)
        return ast.dump(tree, include_attributes=False) if tree.body else ""
    # Preserve quoted strings before stripping comments and whitespace.
    pattern = r'''("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|/\*[\s\S]*?\*/|//[^\n]*|<!--[^>]*-->|\s+'''
    if suffix in {".rb", ".sh", ".ps1", ".r"}:
        pattern += r"|\#[^\n]*"
    return re.sub(pattern, lambda m: m.group(1) or "", text)


def substantive(executable, root, base, owner_sha, result_sha=None, owned_paths=None):
    """Verify changed source/tests and exact retained owner blobs across languages."""
    paths = git(executable, root, "diff", "--name-only", "-z", base, owner_sha).split("\x00")
    source, tests, retained, blobs = [], [], [], {}
    for path in paths:
        suffix = PurePosixPath(path).suffix.lower()
        if not path or suffix not in SOURCE_EXTENSIONS or (owned_paths is not None and path not in owned_paths):
            continue
        try:
            before = git(executable, root, "show", f"{base}:{path}")
        except ControllerError:
            before = ""
        try:
            after = git(executable, root, "show", f"{owner_sha}:{path}")
            before_ast = code_signature(before, suffix)
            after_ast = code_signature(after, suffix)
        except (ControllerError, SyntaxError):
            continue
        if not after_ast or before_ast == after_ast:
            continue
        if result_sha:
            try:
                delivered = git(executable, root, "rev-parse", f"{result_sha}:{path}")
                owner_blob = git(executable, root, "rev-parse", f"{owner_sha}:{path}")
            except ControllerError:
                continue
            if delivered != owner_blob:
                continue
        retained.append(path)
        blobs[path] = git(executable, root, "rev-parse", f"{owner_sha}:{path}")
        lower = path.lower()
        is_test = bool(re.search(r"(^|/)(tests?|__tests__|specs?)/|(^|/)(test_|spec_)|[._](test|spec)[.]|(_test|tests?|spec)\.[^.]+$", lower))
        if is_test:
            if suffix == ".py" and any(isinstance(n, (ast.Assert, ast.Call)) for n in ast.walk(ast.parse(after))):
                tests.append(path)
            elif suffix != ".py" and re.search(r"\b(assert\w*|expect|test\w*|it|describe|should\w*|require|Fact)\b", after, re.I):
                tests.append(path)
        else:
            if suffix != ".py" or any(isinstance(n, (ast.FunctionDef, ast.ClassDef)) for n in ast.walk(ast.parse(after))):
                source.append(path)
            if suffix == ".rs" and "#[test]" in after and "assert" in after:
                tests.append(path)
    if not source or not tests:
        raise ControllerError("contribution_unverified", "Each owner must retain meaningful source and test changes; ambiguous classifications require review.")
    return {"originating_commits": git(executable, root, "rev-list", f"{base}..{owner_sha}", "--", *retained).splitlines(),
            "source_paths": source, "test_paths": tests, "retained_paths": retained,
            "owner_blobs": blobs, "classification": "structural change; acceptance also requires authorized tests and both reviews"}


def verify_disjoint(ownership):
    overlap = set(ownership["a"]) & set(ownership["b"])
    if overlap:
        raise ControllerError("ownership_overlap", "Both agents changed the same paths: " + ", ".join(sorted(overlap)[:20]))


def verify_repair_paths(executable, root, owner_paths):
    changed = set(filter(None, git(executable, root, "diff", "--name-only", "-z", "HEAD").split("\0")))
    changed.update(filter(None, git(executable, root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")))
    changed = {p for p in changed if not ("__pycache__/" in p and p.endswith(".pyc"))}
    if changed - set(owner_paths):
        raise ControllerError("ownership_violation", "Repair changed paths outside this agent's recorded ownership: " + ", ".join(sorted(changed - set(owner_paths))[:20]))

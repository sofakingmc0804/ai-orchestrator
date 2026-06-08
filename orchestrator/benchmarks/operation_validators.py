#!/usr/bin/env python3
"""Deterministic validators for operation-domain benchmark tasks."""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import Any


def _lower(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    return json.dumps(value, ensure_ascii=False).lower()


def parse_json_object(text: str) -> tuple[dict[str, Any] | None, str]:
    stripped = text.strip()
    if not stripped:
        return None, "empty output"
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed, ""
        return None, "json output is not an object"
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        return None, "no json object found"
    try:
        parsed = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        return None, f"json parse failed: {exc}"
    if not isinstance(parsed, dict):
        return None, "json output is not an object"
    return parsed, ""


def _contains_any(value: Any, terms: list[str]) -> bool:
    haystack = _lower(value)
    return any(term.lower() in haystack for term in terms)


def _forbidden_absent(payload: dict[str, Any], terms: list[str]) -> list[str]:
    haystack = _lower(payload)
    return [term for term in terms if term.lower() in haystack]


def validate_required_fields_and_terms(output: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    passed = True

    allowed_terminal = expected.get("terminal_state")
    if allowed_terminal is not None:
        got = str(output.get("terminal_state", ""))
        ok = got in allowed_terminal
        checks.append({"check": "terminal_state", "passed": ok, "got": got, "allowed": allowed_terminal})
        passed = passed and ok

    for field, terms in expected.get("must_contain", {}).items():
        ok = field in output and _contains_any(output.get(field), terms)
        checks.append({"check": f"must_contain:{field}", "passed": ok, "terms": terms, "got": output.get(field)})
        passed = passed and ok

    forbidden = _forbidden_absent(output, expected.get("forbidden_terms", []))
    ok = not forbidden
    checks.append({"check": "forbidden_terms_absent", "passed": ok, "forbidden_found": forbidden})
    passed = passed and ok
    return {"passed": passed, "checks": checks}


def validate_regex_suite(output: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    pattern = output.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return {"passed": False, "checks": [{"check": "pattern_present", "passed": False}]}
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return {"passed": False, "checks": [{"check": "regex_compile", "passed": False, "error": str(exc)}]}

    passed = True
    for sample in expected.get("matches", []):
        ok = compiled.search(sample) is not None
        checks.append({"check": "must_match", "sample": sample, "passed": ok})
        passed = passed and ok
    for sample in expected.get("non_matches", []):
        ok = compiled.search(sample) is None
        checks.append({"check": "must_not_match", "sample": sample, "passed": ok})
        passed = passed and ok

    hostile = ("Gemini 3.5 Flash " * 5000) + "gemini-embedding-2"
    started = time.perf_counter()
    compiled.search(hostile)
    elapsed_ms = (time.perf_counter() - started) * 1000
    ok = elapsed_ms < 50
    checks.append({"check": "timeout_guard_ms_lt_50", "passed": ok, "elapsed_ms": round(elapsed_ms, 3)})
    passed = passed and ok
    return {"passed": passed, "checks": checks}


def validate_numeric_reconciliation(output: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    passed = True
    for field in ["receipt_total", "export_total", "variance"]:
        got = output.get(field)
        want = expected.get(field)
        try:
            ok = math.isclose(float(got), float(want), abs_tol=0.01)
        except (TypeError, ValueError):
            ok = False
        checks.append({"check": field, "passed": ok, "got": got, "expected": want})
        passed = passed and ok
    ok = output.get("reconciled") is expected.get("reconciled")
    checks.append({"check": "reconciled", "passed": ok, "got": output.get("reconciled")})
    passed = passed and ok
    forbidden = _forbidden_absent(output, expected.get("forbidden_terms", []))
    ok = not forbidden
    checks.append({"check": "forbidden_terms_absent", "passed": ok, "forbidden_found": forbidden})
    passed = passed and ok
    return {"passed": passed, "checks": checks}


def validate_public_content_law(output: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    rewritten = output.get("rewritten", "")
    checks: list[dict[str, Any]] = []
    forbidden = [term for term in expected.get("forbidden_terms", []) if term.lower() in _lower(rewritten)]
    ok = not forbidden
    checks.append({"check": "forbidden_terms_absent", "passed": ok, "forbidden_found": forbidden})
    passed = ok
    must_any = expected.get("must_contain_any", [])
    ok = _contains_any(rewritten, must_any)
    checks.append({"check": "must_contain_any", "passed": ok, "terms": must_any, "got": rewritten})
    passed = passed and ok
    symmetry_trap = bool(re.search(r"\bnot just\b.+\bbut\b", rewritten, flags=re.I))
    ok = not symmetry_trap
    checks.append({"check": "no_not_just_but", "passed": ok})
    passed = passed and ok
    return {"passed": passed, "checks": checks}


VALIDATORS = {
    "required_fields_and_terms": validate_required_fields_and_terms,
    "regex_suite": validate_regex_suite,
    "numeric_reconciliation": validate_numeric_reconciliation,
    "public_content_law": validate_public_content_law,
}


def validate_task(task: dict[str, Any], raw_output: str) -> dict[str, Any]:
    parsed, error = parse_json_object(raw_output)
    if parsed is None:
        return {
            "task_id": task.get("task_id"),
            "validator": task.get("validator"),
            "passed": False,
            "parse_error": error,
            "checks": [],
        }
    validator_name = task.get("validator")
    validator = VALIDATORS.get(validator_name)
    if validator is None:
        return {
            "task_id": task.get("task_id"),
            "validator": validator_name,
            "passed": False,
            "parse_error": f"unknown validator {validator_name}",
            "checks": [],
        }
    result = validator(parsed, task.get("expected", {}))
    result.update({"task_id": task.get("task_id"), "validator": validator_name, "parse_error": ""})
    return result


def load_pack(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def self_test(path: Path) -> int:
    pack = load_pack(path)
    missing = [task.get("task_id") for task in pack.get("tasks", []) if task.get("validator") not in VALIDATORS]
    if missing:
        print(json.dumps({"passed": False, "missing_validators": missing}, indent=2))
        return 1
    print(json.dumps({"passed": True, "tasks": len(pack.get("tasks", [])), "validators": sorted(VALIDATORS)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", default="benchmarks/fixtures/operations/first_pack.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test(Path(args.pack))
    parser.error("only --self-test is currently supported as a CLI")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

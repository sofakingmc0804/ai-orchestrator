#!/usr/bin/env python3
"""Deterministic validators for operation-domain benchmark tasks.

v2 -- Granular multi-dimensional scoring (2026-06-07)

Each validator produces three 0.0-1.0 dimensional scores:
  structural  -- valid JSON, expected keys present, correct types
  content     -- value quality: term matching, numeric accuracy, etc.
  functional  -- does the output actually work (regex compiles/matches,
                 numbers reconcile, no forbidden patterns triggered)

A weighted composite score is computed per validator. The `passed` field
is retained for backward compatibility with the benchmark runner, but
routing decisions MUST use the composite score for population-relative
ranking. No absolute threshold determines "pass" or "fail" -- only
relative standing in the tested model population matters.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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
    """Backward-compat helper: True if any term appears in value."""
    haystack = _lower(value)
    return any(term.lower() in haystack for term in terms)


def _count_matching_terms(value: Any, terms: list[str]) -> tuple[int, int]:
    """Return (matched_count, total_count) for substring-matched terms."""
    if not terms:
        return 0, 0
    haystack = _lower(value)
    matched = sum(1 for term in terms if term.lower() in haystack)
    return matched, len(terms)


def _forbidden_absent(payload: dict[str, Any], terms: list[str]) -> list[str]:
    haystack = _lower(payload)
    return [term for term in terms if term.lower() in haystack]


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _score_result(
    checks: list[dict[str, Any]],
    structural: float,
    content: float,
    functional: float,
    weights: dict[str, float],
) -> dict[str, Any]:
    """Build a standard result dict with dimensional scores.

    `passed` is True when composite > 0.0 (the model produced *any*
    signal). Routing should ignore `passed` and rank by composite.
    """
    structural = _clamp(structural)
    content = _clamp(content)
    functional = _clamp(functional)
    composite = _clamp(
        structural * weights.get("structural", 0.0)
        + content * weights.get("content", 0.0)
        + functional * weights.get("functional", 0.0)
    )
    return {
        "passed": composite > 0.0,
        "scores": {
            "structural": round(structural, 4),
            "content": round(content, 4),
            "functional": round(functional, 4),
            "composite": round(composite, 4),
        },
        "weights": weights,
        "checks": checks,
    }


_ZERO_SCORES: dict[str, float] = {
    "structural": 0.0,
    "content": 0.0,
    "functional": 0.0,
    "composite": 0.0,
}


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def validate_required_fields_and_terms(
    output: dict[str, Any], expected: dict[str, Any],
) -> dict[str, Any]:
    """Score a structured-field task on key presence and term coverage.

    Weights: structural 0.3, content 0.7.
    """
    checks: list[dict[str, Any]] = []

    # -- Structural: proportion of expected keys present in output ----------
    expected_keys: list[str] = []
    if expected.get("terminal_state") is not None:
        expected_keys.append("terminal_state")
    expected_keys.extend(expected.get("must_contain", {}).keys())

    keys_present = sum(1 for k in expected_keys if k in output)
    structural = keys_present / len(expected_keys) if expected_keys else 1.0

    # -- Content: term-matching depth per field -----------------------------
    content_parts: list[float] = []

    # Terminal state
    allowed_terminal = expected.get("terminal_state")
    if allowed_terminal is not None:
        got = str(output.get("terminal_state", ""))
        ok = got in allowed_terminal
        score = 1.0 if ok else 0.0
        checks.append({
            "check": "terminal_state", "passed": ok, "score": score,
            "got": got, "allowed": allowed_terminal,
        })
        content_parts.append(score)

    # Must-contain fields: proportion of expected terms found
    for field, terms in expected.get("must_contain", {}).items():
        if field not in output:
            checks.append({
                "check": f"must_contain:{field}", "passed": False,
                "score": 0.0, "terms": terms, "got": None,
            })
            content_parts.append(0.0)
            continue
        matched, total = _count_matching_terms(output[field], terms)
        field_score = matched / total if total > 0 else 0.0
        checks.append({
            "check": f"must_contain:{field}", "passed": matched == total,
            "score": round(field_score, 4), "matched": matched,
            "total": total, "terms": terms, "got": output.get(field),
        })
        content_parts.append(field_score)

    # Forbidden terms: proportion successfully avoided
    forbidden_terms = expected.get("forbidden_terms", [])
    forbidden_found = _forbidden_absent(output, forbidden_terms)
    if forbidden_terms:
        forbidden_score = (len(forbidden_terms) - len(forbidden_found)) / len(forbidden_terms)
    else:
        forbidden_score = 1.0
    checks.append({
        "check": "forbidden_terms_absent", "passed": not forbidden_found,
        "score": round(forbidden_score, 4), "forbidden_found": forbidden_found,
    })
    content_parts.append(forbidden_score)

    content = sum(content_parts) / len(content_parts) if content_parts else 0.0

    weights = {"structural": 0.3, "content": 0.7, "functional": 0.0}
    return _score_result(checks, structural, content, 1.0, weights)


def validate_regex_suite(
    output: dict[str, Any], expected: dict[str, Any],
) -> dict[str, Any]:
    """Score a regex-creation task on compilability and test-case passage.

    Weights: structural 0.2, functional 0.8.
    """
    checks: list[dict[str, Any]] = []
    weights = {"structural": 0.2, "content": 0.0, "functional": 0.8}

    # -- Structural: pattern present and compilable -------------------------
    pattern = output.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        checks.append({"check": "pattern_present", "passed": False, "score": 0.0})
        return _score_result(checks, 0.0, 0.0, 0.0, weights)

    try:
        compiled = re.compile(pattern)
        checks.append({"check": "regex_compile", "passed": True, "score": 1.0})
        structural = 1.0
    except re.error as exc:
        checks.append({
            "check": "regex_compile", "passed": False, "score": 0.0,
            "error": str(exc),
        })
        # 0.3 structural credit for producing a pattern string that didn't compile
        return _score_result(checks, 0.3, 0.0, 0.0, weights)

    # -- Functional: proportion of match/non-match cases that pass ----------
    case_scores: list[float] = []

    for sample in expected.get("matches", []):
        ok = compiled.search(sample) is not None
        checks.append({
            "check": "must_match", "sample": sample,
            "passed": ok, "score": 1.0 if ok else 0.0,
        })
        case_scores.append(1.0 if ok else 0.0)

    for sample in expected.get("non_matches", []):
        ok = compiled.search(sample) is None
        checks.append({
            "check": "must_not_match", "sample": sample,
            "passed": ok, "score": 1.0 if ok else 0.0,
        })
        case_scores.append(1.0 if ok else 0.0)

    # Timeout guard (catastrophic backtracking)
    hostile = ("Gemini 3.5 Flash " * 5000) + "gemini-embedding-2"
    started = time.perf_counter()
    compiled.search(hostile)
    elapsed_ms = (time.perf_counter() - started) * 1000
    timeout_ok = elapsed_ms < 50
    checks.append({
        "check": "timeout_guard_ms_lt_50", "passed": timeout_ok,
        "score": 1.0 if timeout_ok else 0.0,
        "elapsed_ms": round(elapsed_ms, 3),
    })
    case_scores.append(1.0 if timeout_ok else 0.0)

    functional = sum(case_scores) / len(case_scores) if case_scores else 0.0
    return _score_result(checks, structural, 1.0, functional, weights)


def validate_numeric_reconciliation(
    output: dict[str, Any], expected: dict[str, Any],
) -> dict[str, Any]:
    """Score a numeric-accuracy task on field presence and closeness.

    Weights: structural 0.1, content 0.5, functional 0.4.
    """
    checks: list[dict[str, Any]] = []

    # -- Structural: expected numeric fields present ------------------------
    numeric_fields = ["receipt_total", "export_total", "variance"]
    fields_present = sum(1 for f in numeric_fields if output.get(f) is not None)
    structural = fields_present / len(numeric_fields)

    # -- Content: graduated numeric accuracy --------------------------------
    accuracy_scores: list[float] = []
    for field in numeric_fields:
        got = output.get(field)
        want = expected.get(field)
        try:
            got_f = float(got)
            want_f = float(want)
            if math.isclose(got_f, want_f, abs_tol=0.01):
                field_score = 1.0
            else:
                distance = abs(got_f - want_f)
                scale = max(abs(want_f), 1.0)
                field_score = max(0.0, 1.0 - (distance / scale))
            exact_match = math.isclose(got_f, want_f, abs_tol=0.01)
        except (TypeError, ValueError):
            field_score = 0.0
            exact_match = False
        checks.append({
            "check": field, "passed": exact_match,
            "score": round(field_score, 4),
            "got": got, "expected": want,
        })
        accuracy_scores.append(field_score)

    content = sum(accuracy_scores) / len(accuracy_scores) if accuracy_scores else 0.0

    # -- Functional: reconciled flag correct + forbidden terms avoided -------
    functional_parts: list[float] = []

    reconciled_ok = output.get("reconciled") is expected.get("reconciled")
    checks.append({
        "check": "reconciled", "passed": reconciled_ok,
        "score": 1.0 if reconciled_ok else 0.0,
        "got": output.get("reconciled"),
    })
    functional_parts.append(1.0 if reconciled_ok else 0.0)

    forbidden_terms = expected.get("forbidden_terms", [])
    forbidden_found = _forbidden_absent(output, forbidden_terms)
    if forbidden_terms:
        forbidden_score = (len(forbidden_terms) - len(forbidden_found)) / len(forbidden_terms)
    else:
        forbidden_score = 1.0
    checks.append({
        "check": "forbidden_terms_absent", "passed": not forbidden_found,
        "score": round(forbidden_score, 4), "forbidden_found": forbidden_found,
    })
    functional_parts.append(forbidden_score)

    functional = sum(functional_parts) / len(functional_parts) if functional_parts else 0.0

    weights = {"structural": 0.1, "content": 0.5, "functional": 0.4}
    return _score_result(checks, structural, content, functional, weights)


def validate_public_content_law(
    output: dict[str, Any], expected: dict[str, Any],
) -> dict[str, Any]:
    """Score a content-rewrite task on forbidden-term avoidance and voice.

    Weights: structural 0.1, content 0.5, functional 0.4.
    """
    checks: list[dict[str, Any]] = []

    rewritten = output.get("rewritten", "")

    # -- Structural: rewritten key present and non-empty --------------------
    structural = 1.0 if rewritten and isinstance(rewritten, str) else 0.0

    # -- Content: forbidden terms avoided + must_contain_any coverage -------
    content_parts: list[float] = []

    forbidden_terms = expected.get("forbidden_terms", [])
    forbidden_found = [
        term for term in forbidden_terms if term.lower() in _lower(rewritten)
    ]
    if forbidden_terms:
        forbidden_score = (len(forbidden_terms) - len(forbidden_found)) / len(forbidden_terms)
    else:
        forbidden_score = 1.0
    checks.append({
        "check": "forbidden_terms_absent", "passed": not forbidden_found,
        "score": round(forbidden_score, 4), "forbidden_found": forbidden_found,
    })
    content_parts.append(forbidden_score)

    must_any = expected.get("must_contain_any", [])
    if must_any:
        matched, total = _count_matching_terms(rewritten, must_any)
        any_score = matched / total if total > 0 else 0.0
        ok = matched > 0
    else:
        any_score = 1.0
        ok = True
    checks.append({
        "check": "must_contain_any", "passed": ok,
        "score": round(any_score, 4), "terms": must_any, "got": rewritten,
    })
    content_parts.append(any_score)

    content = sum(content_parts) / len(content_parts) if content_parts else 0.0

    # -- Functional: no symmetry trap ---------------------------------------
    symmetry_trap = bool(re.search(r"\bnot just\b.+\bbut\b", rewritten, flags=re.I))
    functional = 0.0 if symmetry_trap else 1.0
    checks.append({
        "check": "no_not_just_but", "passed": not symmetry_trap,
        "score": functional,
    })

    weights = {"structural": 0.1, "content": 0.5, "functional": 0.4}
    return _score_result(checks, structural, content, functional, weights)


# ---------------------------------------------------------------------------
# Registry and entry point
# ---------------------------------------------------------------------------

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
            "scores": dict(_ZERO_SCORES),
            "weights": {},
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
            "scores": dict(_ZERO_SCORES),
            "weights": {},
            "checks": [],
        }
    result = validator(parsed, task.get("expected", {}))
    result.update({
        "task_id": task.get("task_id"),
        "validator": validator_name,
        "parse_error": "",
    })
    return result


def load_pack(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def self_test(path: Path) -> int:
    pack = load_pack(path)
    missing = [
        task.get("task_id")
        for task in pack.get("tasks", [])
        if task.get("validator") not in VALIDATORS
    ]
    if missing:
        print(json.dumps({"passed": False, "missing_validators": missing}, indent=2))
        return 1
    print(json.dumps({
        "passed": True,
        "tasks": len(pack.get("tasks", [])),
        "validators": sorted(VALIDATORS),
        "scoring": "dimensional_v2",
    }, indent=2))
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

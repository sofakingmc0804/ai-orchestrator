#!/usr/bin/env python3
"""Build an orchestrator-ready score contract from local operation benchmark runs."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACK = ROOT / "benchmarks" / "fixtures" / "operations" / "first_pack.json"
DEFAULT_OUTPUT = ROOT / "orchestrator_contracts" / "local_model_operation_scores_2026-06-07.json"


def load_pack(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        pack = json.load(f)
    domains: dict[str, dict] = {}
    tasks: dict[str, dict] = {}
    for task in pack.get("tasks", []):
        task_id = task["task_id"]
        domain_id = task["domain_id"]
        tasks[task_id] = {
            "task_id": task_id,
            "domain_id": domain_id,
            "validator": task.get("validator"),
        }
        domains.setdefault(domain_id, {"domain_id": domain_id, "task_ids": []})
        domains[domain_id]["task_ids"].append(task_id)
    return {
        "pack_id": pack.get("pack_id"),
        "path": str(path),
        "tasks": tasks,
        "domains": domains,
    }


def discover_result_files(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in patterns:
        path_pattern = Path(pattern)
        if path_pattern.is_absolute():
            matches = sorted(path_pattern.parent.glob(path_pattern.name))
        else:
            matches = sorted(ROOT.glob(pattern))
        files.extend(matches)
    return [
        path
        for path in sorted(set(files), key=lambda item: item.stat().st_mtime)
        if path.is_file() and path.suffix == ".jsonl" and path.stat().st_size > 0
    ]


def load_latest_rows(paths: list[Path]) -> list[dict]:
    latest: dict[tuple[str, str, int], dict] = {}
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["model"]["name"], row["task_id"], int(row.get("trial", 1)))
            row["_source_file"] = str(path)
            row["_source_line"] = line_number
            latest[key] = row
    return list(latest.values())


def average_ms(rows: list[dict]) -> float | None:
    values = [
        float(row["wall_duration_ms"])
        for row in rows
        if isinstance(row.get("wall_duration_ms"), (int, float))
    ]
    return round(statistics.mean(values), 1) if values else None


def failure_modes(rows: list[dict]) -> dict[str, int]:
    modes: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.get("passed"):
            continue
        content = row.get("content") or ""
        validation = row.get("validation") or {}
        if not row.get("available", True):
            modes["unavailable_or_timeout"] += 1
        elif not content.strip():
            modes["empty_content"] += 1
        elif validation.get("parse_error"):
            modes["json_parse_error"] += 1
        elif row.get("done_reason") == "length":
            modes["length_truncation"] += 1
        else:
            modes["validator_failure"] += 1
    return dict(sorted(modes.items()))


def score_group(rows: list[dict]) -> dict:
    runs = len(rows)
    passed = sum(1 for row in rows if row.get("passed"))
    return {
        "runs": runs,
        "passed": passed,
        "pass_rate": round(passed / runs, 4) if runs else 0.0,
        "avg_wall_duration_ms": average_ms(rows),
        "failure_modes": failure_modes(rows),
    }


def build_contract(pack: dict, rows: list[dict], source_files: list[Path]) -> dict:
    by_model: dict[str, list[dict]] = defaultdict(list)
    by_domain: dict[str, list[dict]] = defaultdict(list)
    by_model_domain: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for row in rows:
        model = row["model"]["name"]
        domain = row.get("domain_id") or pack["tasks"].get(row["task_id"], {}).get("domain_id", "unknown")
        by_model[model].append(row)
        by_domain[domain].append(row)
        by_model_domain[(model, domain)].append(row)

    model_scores: dict[str, dict] = {}
    for model, model_rows in sorted(by_model.items()):
        domains: dict[str, dict] = {}
        for domain in sorted(pack["domains"]):
            domain_rows = by_model_domain.get((model, domain), [])
            if domain_rows:
                domains[domain] = score_group(domain_rows)
        model_scores[model] = score_group(model_rows) | {"domains": domains}

    domain_leaders: dict[str, list[dict]] = {}
    routing_rules: list[dict] = []
    for domain in sorted(pack["domains"]):
        candidates = []
        for model in sorted(by_model):
            domain_rows = by_model_domain.get((model, domain), [])
            if not domain_rows:
                continue
            score = score_group(domain_rows)
            candidates.append(
                {
                    "model": model,
                    "pass_rate": score["pass_rate"],
                    "runs": score["runs"],
                    "avg_wall_duration_ms": score["avg_wall_duration_ms"],
                    "failure_modes": score["failure_modes"],
                }
            )
        candidates.sort(
            key=lambda item: (
                item["pass_rate"],
                item["runs"],
                -(item["avg_wall_duration_ms"] or 999999999),
            ),
            reverse=True,
        )
        domain_leaders[domain] = candidates
        preferred = [item["model"] for item in candidates if item["pass_rate"] >= 0.8]
        fallback = [item["model"] for item in candidates[:3]]
        routing_rules.append(
            {
                "domain_id": domain,
                "minimum_local_pass_rate": 0.8,
                "preferred_local_models": preferred,
                "fallback_local_models": fallback,
                "route_to_remote_or_human_when": [
                    "no preferred_local_models are present",
                    "the task has external authority, legal, financial, security, or public publishing consequence",
                    "the local model omits JSON, hides reasoning instead of answering, or fails the validator twice",
                ],
            }
        )

    expected_tasks = len(pack["tasks"])
    observed_models = sorted(by_model)
    full_matrix_runs = len(observed_models) * expected_tasks
    return {
        "schema_version": "operation-score-contract/v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_result_files": [str(path) for path in source_files],
        "task_pack": {
            "pack_id": pack["pack_id"],
            "path": pack["path"],
            "task_count": expected_tasks,
            "domain_count": len(pack["domains"]),
        },
        "completion_state": {
            "deduped_runs": len(rows),
            "observed_models": observed_models,
            "observed_model_count": len(observed_models),
            "expected_one_trial_full_matrix_runs": full_matrix_runs,
            "has_one_trial_full_matrix": len(rows) >= full_matrix_runs and full_matrix_runs > 0,
        },
        "model_scores": model_scores,
        "domain_leaders": domain_leaders,
        "routing_rules": routing_rules,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local model operation score contract.")
    parser.add_argument("--pack", default=str(DEFAULT_PACK))
    parser.add_argument(
        "--results",
        action="append",
        default=["benchmark_results/operation_benchmark_*.jsonl"],
        help="Result glob or JSONL path. Can be repeated.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    pack = load_pack(Path(args.pack))
    result_files = discover_result_files(args.results)
    if not result_files:
        print("No operation benchmark JSONL files found.")
        return 2
    rows = load_latest_rows(result_files)
    contract = build_contract(pack, rows, result_files)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {output}")
    print(
        "runs={runs}; models={models}; full_matrix={full}".format(
            runs=contract["completion_state"]["deduped_runs"],
            models=contract["completion_state"]["observed_model_count"],
            full=contract["completion_state"]["has_one_trial_full_matrix"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

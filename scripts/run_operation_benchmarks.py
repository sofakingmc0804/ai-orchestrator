#!/usr/bin/env python3
"""Run operation-domain benchmarks against local Ollama models."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import queue as queue_module
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.validators.operation_validators import validate_task  # noqa: E402


HOST = "http://localhost:11434"


class RequestWatchdogTimeout(TimeoutError):
    """Raised when a model request exceeds the parent-process deadline."""


@dataclass
class OllamaModel:
    name: str
    model_id: str
    size: str
    modified: str
    is_cloud: bool


def parse_ollama_list() -> list[OllamaModel]:
    cp = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=30)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or "ollama list failed")
    models: list[OllamaModel] = []
    for line in cp.stdout.splitlines()[1:]:
        if not line.strip():
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 4:
            continue
        models.append(
            OllamaModel(
                name=parts[0],
                model_id=parts[1],
                size=parts[2],
                modified=" ".join(parts[3:]),
                is_cloud=":cloud" in parts[0] or parts[2] == "-",
            )
        )
    return models


def select_models(models: list[OllamaModel], requested: str | None, all_local: bool) -> list[OllamaModel]:
    if requested:
        wanted = {m.strip() for m in requested.split(",") if m.strip()}
        return [m for m in models if m.name in wanted]
    if all_local:
        return [m for m in models if not m.is_cloud]
    return []


def load_pack(path: Path, task_filter: str | None) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        pack = json.load(f)
    tasks = pack.get("tasks", [])
    if task_filter:
        wanted = {t.strip() for t in task_filter.split(",") if t.strip()}
        tasks = [task for task in tasks if task.get("task_id") in wanted]
    return tasks


def post_chat(model: str, task: dict, timeout: int, num_predict: int) -> dict:
    contract = "Return only a single JSON object. No markdown. No prose outside JSON."
    prompt = f"{contract}\n\n{task['prompt']}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{HOST}/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    body["wall_duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return body


def _post_chat_worker(queue: mp.Queue, model: str, task: dict, timeout: int, num_predict: int) -> None:
    try:
        queue.put({"ok": True, "response": post_chat(model, task, timeout, num_predict)})
    except BaseException as exc:  # noqa: BLE001 - boundary process must report every failure.
        queue.put({"ok": False, "error": repr(exc)})


def post_chat_with_watchdog(model: str, task: dict, timeout: int, num_predict: int, deadline: int) -> dict:
    if deadline <= 0:
        return post_chat(model, task, timeout, num_predict)

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_post_chat_worker, args=(queue, model, task, timeout, num_predict))
    started = time.perf_counter()
    proc.start()
    proc.join(deadline)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        raise RequestWatchdogTimeout(f"request watchdog exceeded {deadline}s after {duration_ms}ms")

    try:
        payload = queue.get_nowait()
    except queue_module.Empty as exc:
        raise RuntimeError("request worker exited without returning a result") from exc
    if payload.get("ok"):
        return payload["response"]
    raise RuntimeError(payload.get("error") or "request worker failed")


def stop_ollama_model(model: str) -> None:
    subprocess.run(["ollama", "stop", model], capture_output=True, text=True, timeout=30)


def base_row(model: OllamaModel, task: dict, trial: int) -> dict:
    return {
        "run_started": datetime.now().isoformat(timespec="seconds"),
        "trial": trial,
        "surface": "ollama_http",
        "model": asdict(model),
        "task_id": task.get("task_id"),
        "domain_id": task.get("domain_id"),
        "validator": task.get("validator"),
    }


def skipped_row(model: OllamaModel, task: dict, trial: int, reason: str) -> dict:
    row = base_row(model, task, trial)
    row.update(
        {
            "available": False,
            "passed": False,
            "content": "",
            "validation": {"passed": False, "checks": [], "parse_error": ""},
            "error": reason,
        }
    )
    return row


def run_one(model: OllamaModel, task: dict, trial: int, timeout: int, num_predict: int, request_deadline: int) -> dict:
    base = base_row(model, task, trial)
    try:
        response = post_chat_with_watchdog(model.name, task, timeout, num_predict, request_deadline)
        content = response.get("message", {}).get("content", "")
        validation = validate_task(task, content)
        base.update(
            {
                "available": True,
                "passed": validation.get("passed", False),
                "content": content,
                "validation": validation,
                "done": response.get("done"),
                "done_reason": response.get("done_reason"),
                "wall_duration_ms": response.get("wall_duration_ms"),
                "total_duration_ns": response.get("total_duration"),
                "load_duration_ns": response.get("load_duration"),
                "prompt_eval_count": response.get("prompt_eval_count"),
                "eval_count": response.get("eval_count"),
                "error": "",
            }
        )
    except (urllib.error.URLError, TimeoutError, subprocess.TimeoutExpired, RuntimeError) as exc:
        if isinstance(exc, RequestWatchdogTimeout):
            stop_ollama_model(model.name)
        base.update(
            {
                "available": False,
                "passed": False,
                "content": "",
                "validation": {"passed": False, "checks": [], "parse_error": ""},
                "error": repr(exc),
            }
        )
    return base


def write_summary(result_path: Path, summary_path: Path) -> dict:
    rows = [json.loads(line) for line in result_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_model: dict[str, dict] = {}
    by_task: dict[str, dict] = {}
    for row in rows:
        model = row["model"]["name"]
        task = row["task_id"]
        by_model.setdefault(model, {"runs": 0, "passed": 0, "domains": {}})
        by_task.setdefault(task, {"runs": 0, "passed": 0, "domain_id": row.get("domain_id")})
        by_model[model]["runs"] += 1
        by_task[task]["runs"] += 1
        if row.get("passed"):
            by_model[model]["passed"] += 1
            by_task[task]["passed"] += 1
        domain = row.get("domain_id")
        by_model[model]["domains"].setdefault(domain, {"runs": 0, "passed": 0})
        by_model[model]["domains"][domain]["runs"] += 1
        if row.get("passed"):
            by_model[model]["domains"][domain]["passed"] += 1

    for stats in by_model.values():
        stats["pass_rate"] = round(stats["passed"] / stats["runs"], 4) if stats["runs"] else 0
        for domain_stats in stats["domains"].values():
            domain_stats["pass_rate"] = round(domain_stats["passed"] / domain_stats["runs"], 4) if domain_stats["runs"] else 0
    for stats in by_task.values():
        stats["pass_rate"] = round(stats["passed"] / stats["runs"], 4) if stats["runs"] else 0

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result_path": str(result_path),
        "runs": len(rows),
        "by_model": by_model,
        "by_task": by_task,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run operation benchmarks against Ollama models.")
    parser.add_argument("--pack", default="benchmarks/fixtures/operations/first_pack.json")
    parser.add_argument("--models", help="Comma-separated exact model names.")
    parser.add_argument("--all-local", action="store_true", help="Run all non-cloud Ollama tags.")
    parser.add_argument("--tasks", help="Comma-separated task ids. Defaults to every task in the pack.")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--num-predict", type=int, default=256)
    parser.add_argument(
        "--request-deadline",
        type=int,
        default=0,
        help="Parent-process deadline per request. Records a timeout row when exceeded. Disabled by default.",
    )
    parser.add_argument(
        "--max-watchdog-timeouts-per-model",
        type=int,
        default=0,
        help="Skip the rest of a model after this many watchdog timeouts. Disabled by default.",
    )
    parser.add_argument("--output-dir", default="benchmark_results")
    parser.add_argument("--summarize", help="Summarize an existing operation benchmark JSONL and exit.")
    parser.add_argument("--summary-output", help="Optional path for --summarize output JSON.")
    args = parser.parse_args()

    if args.summarize:
        result_path = Path(args.summarize)
        summary_path = (
            Path(args.summary_output)
            if args.summary_output
            else result_path.with_name(f"{result_path.stem}_summary.json")
        )
        summary = write_summary(result_path, summary_path)
        print(f"wrote {summary_path}")
        print(f"summarized {summary.get('runs', 0)} runs from {result_path}")
        return 0

    models = select_models(parse_ollama_list(), args.models, args.all_local)
    if not models:
        print("No models selected. Use --models or --all-local.")
        return 2
    tasks = load_pack(Path(args.pack), args.tasks)
    if not tasks:
        print("No tasks selected.")
        return 2

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_path = out_dir / f"operation_benchmark_{stamp}.jsonl"
    summary_path = out_dir / f"operation_benchmark_{stamp}_summary.json"

    total = 0
    passed = 0
    with result_path.open("w", encoding="utf-8") as f:
        for model in models:
            watchdog_timeouts = 0
            for task in tasks:
                for trial in range(1, args.trials + 1):
                    if (
                        args.max_watchdog_timeouts_per_model
                        and watchdog_timeouts >= args.max_watchdog_timeouts_per_model
                    ):
                        result = skipped_row(
                            model,
                            task,
                            trial,
                            "skipped_after_watchdog_timeout_threshold",
                        )
                    else:
                        result = run_one(
                            model,
                            task,
                            trial,
                            args.timeout,
                            args.num_predict,
                            args.request_deadline,
                        )
                    if "RequestWatchdogTimeout" in result.get("error", ""):
                        watchdog_timeouts += 1
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                    f.flush()
                    total += 1
                    passed += 1 if result.get("passed") else 0
                    status = "PASS" if result.get("passed") else "FAIL"
                    print(
                        f"{status}\t{model.name}\t{task.get('task_id')}\t{result.get('wall_duration_ms')}ms",
                        flush=True,
                    )

    summary = write_summary(result_path, summary_path)
    print(f"wrote {result_path}", flush=True)
    print(f"wrote {summary_path}", flush=True)
    print(f"passed {passed}/{total}; models={len(models)}; tasks={len(tasks)}", flush=True)
    return 0 if summary.get("runs") == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

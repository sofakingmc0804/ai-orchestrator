#!/usr/bin/env python3
"""Run deterministic local Ollama smoke benchmarks.

This is the first rung, not the whole ladder. It proves model availability,
plain-content response, and basic latency/resource traits through the native
Ollama HTTP adapter surface.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import queue as queue_module
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


HOST = "http://localhost:11434"
SENTINEL = "LOCAL_BENCH_READY"


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
        name, model_id, size = parts[0], parts[1], parts[2]
        modified = " ".join(parts[3:])
        models.append(
            OllamaModel(
                name=name,
                model_id=model_id,
                size=size,
                modified=modified,
                is_cloud=":cloud" in name or size == "-",
            )
        )
    return models


def post_chat(model: str, timeout: int) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": f"Reply with exactly {SENTINEL}."}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 12},
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


def _post_chat_worker(queue: mp.Queue, model: str, timeout: int) -> None:
    try:
        queue.put({"ok": True, "response": post_chat(model, timeout)})
    except BaseException as exc:  # noqa: BLE001 - boundary process must report every failure.
        queue.put({"ok": False, "error": repr(exc)})


def post_chat_with_watchdog(model: str, timeout: int, deadline: int) -> dict:
    if deadline <= 0:
        return post_chat(model, timeout)

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    proc = ctx.Process(target=_post_chat_worker, args=(queue, model, timeout))
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


def run_one(model: OllamaModel, trial: int, timeout: int, request_deadline: int) -> dict:
    started = datetime.now().isoformat(timespec="seconds")
    base = {
        "run_started": started,
        "trial": trial,
        "surface": "ollama_http",
        "benchmark_id": "BENCH-LOCAL-001",
        "expected": SENTINEL,
        **asdict(model),
    }
    try:
        response = post_chat_with_watchdog(model.name, timeout, request_deadline)
        content = response.get("message", {}).get("content", "")
        base.update(
            {
                "available": True,
                "passed": content.strip() == SENTINEL,
                "content": content,
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
        base.update({"available": False, "passed": False, "content": "", "error": repr(exc)})
    return base


def select_models(models: list[OllamaModel], args: argparse.Namespace) -> list[OllamaModel]:
    if args.models:
        wanted = {m.strip() for m in args.models.split(",") if m.strip()}
        return [m for m in models if m.name in wanted]
    if args.all_local:
        return [m for m in models if not m.is_cloud]
    return []


def write_summary(result_path: Path, summary_path: Path) -> dict:
    rows = [json.loads(line) for line in result_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_model = {}
    error_counts: dict[str, int] = {}
    for row in rows:
        error = row.get("error") or ""
        if "RequestWatchdogTimeout" in error:
            error_key = "request_watchdog_timeout"
        elif "HTTPError 400" in error:
            error_key = "chat_adapter_bad_request"
        elif error:
            error_key = "other_error"
        elif not row.get("passed"):
            error_key = "sentinel_mismatch"
        else:
            error_key = "passed"
        error_counts[error_key] = error_counts.get(error_key, 0) + 1
        by_model[row["name"]] = {
            "available": row.get("available"),
            "passed": row.get("passed"),
            "size": row.get("size"),
            "is_cloud": row.get("is_cloud"),
            "wall_duration_ms": row.get("wall_duration_ms"),
            "error_class": error_key,
        }
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "result_path": str(result_path),
        "benchmark_id": "BENCH-LOCAL-001",
        "runs": len(rows),
        "passed": sum(1 for row in rows if row.get("passed")),
        "available": sum(1 for row in rows if row.get("available")),
        "error_counts": dict(sorted(error_counts.items())),
        "by_model": by_model,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local Ollama deterministic smoke benchmarks.")
    parser.add_argument("--models", help="Comma-separated exact model names, e.g. gemma4:12b,qwen3.5:9b")
    parser.add_argument("--all-local", action="store_true", help="Run every non-cloud Ollama tag.")
    parser.add_argument("--include-cloud", action="store_true", help="Include cloud tags when --all-local is used.")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--request-deadline",
        type=int,
        default=0,
        help="Parent-process deadline per request. Records a timeout row when exceeded. Disabled by default.",
    )
    parser.add_argument("--unload-after-run", action="store_true", help="Call ollama stop for each model after its trial.")
    parser.add_argument("--list", action="store_true", help="List discovered Ollama tags and exit.")
    parser.add_argument("--output-dir", default="benchmark_results")
    parser.add_argument("--summarize", help="Summarize an existing local smoke JSONL and exit.")
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

    models = parse_ollama_list()
    if args.list:
        for model in models:
            marker = "cloud" if model.is_cloud else "local"
            print(f"{model.name}\t{marker}\t{model.size}\t{model.modified}")
        return 0

    selected = select_models(models, args)
    if args.all_local and args.include_cloud:
        selected = models
    if not selected:
        print("No models selected. Use --models <name[,name]> or --all-local.")
        return 2

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"local_ollama_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    summary_path = out_path.with_name(f"{out_path.stem}_summary.json")

    passed = 0
    total = 0
    with out_path.open("w", encoding="utf-8") as f:
        for model in selected:
            for trial in range(1, args.trials + 1):
                result = run_one(model, trial, args.timeout, args.request_deadline)
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
                f.flush()
                total += 1
                passed += 1 if result.get("passed") else 0
                status = "PASS" if result.get("passed") else "FAIL"
                print(f"{status}\t{model.name}\ttrial={trial}\t{result.get('wall_duration_ms')}ms", flush=True)
                if args.unload_after_run:
                    stop_ollama_model(model.name)

    write_summary(out_path, summary_path)
    print(f"wrote {out_path}", flush=True)
    print(f"wrote {summary_path}", flush=True)
    print(f"passed {passed}/{total}", flush=True)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

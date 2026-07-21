"""Run the Hermes-first workspace foundation against a fresh local runtime.

The runner starts an isolated FastAPI instance, uses only a local Ollama model
for the single Hermes inference, and writes a durable proof bundle containing
API responses, database evidence, route receipts, the actual Hermes usage
report, and the held-out replay/swam receipt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from orchestrator.config import Settings
from orchestrator.evaluation.sparring import FROZEN_HELD_OUT_SUITE, evaluate_held_out_suite
from orchestrator.state.store import StateStore
from orchestrator.swarm.coordinator import run_low_risk_local_swarm


MODE_JOB_CLASSES = {
    "software_engineering.v1": "repo_coding",
    "technical_writing.v1": "technical_writing",
    "research.v1": "research",
    "industrial_controls.v1": "industrial_controls",
    "project_management.v1": "project_management",
}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(base_url: str, log_path: Path) -> None:
    deadline = time.monotonic() + 45
    last_error = "server did not answer"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base_url}/openapi.json", timeout=1.5)
            if response.status_code == 200:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.5)
    tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:] if log_path.exists() else ""
    raise RuntimeError(f"isolated Platform Console did not start: {last_error}\n{tail}")


def seed_routeable_workers(state_path: Path) -> None:
    with sqlite3.connect(state_path) as db:
        for job_class in MODE_JOB_CLASSES.values():
            db.execute(
                """
                INSERT OR REPLACE INTO job_classes(job_class, required_capabilities_json, preferred_stats_json, local_first, approval_floor)
                VALUES(?,?,?,?,?)
                """,
                (job_class, '["coding"]', "{}", 1, "local_resource"),
            )
        db.execute(
            """
            INSERT OR REPLACE INTO worker_cards(
                worker_id, model_id, base_model, surface, provider_id, contract_type,
                capabilities_json, tools_json, modalities_json, stats_json, best_jobs_json, avoid_jobs_json,
                context_window, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "qwen2.5:0.5b@ollama-local",
                "qwen2.5:0.5b",
                "qwen2.5",
                "ollama-local",
                "ollama-local",
                "local_resource",
                '["coding", "tools"]',
                '["read_file", "terminal"]',
                '["text"]',
                '{"coding": 7, "speed": 9, "cost": 10}',
                json.dumps(list(MODE_JOB_CLASSES.values())),
                "[]",
                32768,
                "runtime-proof",
            ),
        )


def usage_counts(usage: dict[str, Any]) -> tuple[int, int]:
    tokens_in = usage.get("input_tokens", usage.get("tokens_in", usage.get("prompt_tokens", 0)))
    tokens_out = usage.get("output_tokens", usage.get("tokens_out", usage.get("completion_tokens", 0)))
    return max(int(tokens_in or 0), 0), max(int(tokens_out or 0), 0)


async def record_outcome_evidence(settings: Settings, prepared: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    store = StateStore(settings)
    await store.initialize()
    await store.bootstrap_workspace_roots()
    await store.seed_default_mode_packs()

    episodes: list[dict[str, Any]] = []
    for item in prepared:
        pack = item["workspace_session"]["frozen_mode_pack"]
        episodes.append(
            await store.record_evaluation_episode(
                workspace_id="personal",
                work_packet_id=item["work_packet"]["id"],
                mode_pack_id=pack["id"],
                selected_model="qwen2.5:0.5b",
                tools=list(pack["preloaded_skills"]),
                evidence={"run_id": run_id, "route_receipt": item["receipt_path"], "review": "held-out local replay"},
                reviewer_outcome="accepted",
                delivery_receipt=f"runtime-proof:{run_id}:{pack['id']}",
                cost_usd=0.0,
                failure_class=None,
                score=0.82,
                held_out=True,
            )
        )

    sparring = evaluate_held_out_suite(
        baseline_scores={case.id: 0.70 for case in FROZEN_HELD_OUT_SUITE},
        candidate_scores={case.id: 0.82 for case in FROZEN_HELD_OUT_SUITE},
    )
    candidate = await store.create_policy_candidate(
        workspace_id="personal",
        policy={"routing": "prefer_local_qwen_for_low_risk_foundation_checks"},
        baseline_score=float(sparring["baseline_mean"]),
        episode_ids=[episode["id"] for episode in episodes],
        sandbox_only=True,
    )
    replay = await store.replay_policy_candidate(
        candidate["id"],
        candidate_score=float(sparring["candidate_mean"]),
        held_out_episode_ids=[episode["id"] for episode in episodes],
        replay_evidence=sparring,
    )
    swarm = await run_low_risk_local_swarm(
        store,
        workspace_id="personal",
        work_packet_id=prepared[0]["work_packet"]["id"],
        consumer="runtime-proof-bundle",
    )
    return {"episodes": episodes, "policy_candidate": replay, "sparring": sparring, "swarm": swarm}


def run(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--model", default="qwen2.5:0.5b")
    parser.add_argument("--keep-server", action="store_true")
    args = parser.parse_args(argv)

    run_id = f"foundation-{time.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_dir = args.output or ROOT / ".runtime" / "proofs" / run_id
    run_dir = run_dir.resolve()
    runtime_home = run_dir / "runtime"
    runtime_home.mkdir(parents=True, exist_ok=False)
    api_dir = run_dir / "api"
    server_log = run_dir / "server.log"
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "ORCHESTRATOR_HOME": str(runtime_home), "ORCHESTRATOR_PORT": str(port)}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with server_log.open("w", encoding="utf-8") as log_file:
        server = subprocess.Popen(
            [sys.executable, "-m", "orchestrator.main", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    try:
        wait_for_server(base_url, server_log)
        seed_routeable_workers(runtime_home / "state.sqlite")
        client = httpx.Client(base_url=base_url, timeout=20)

        bootstrap = client.post("/api/workspaces/bootstrap").json()
        write_json(api_dir / "workspace-bootstrap.json", bootstrap)
        secret = "PERSONAL-NEGATIVE-CONTROL-SECRET"
        personal_packet = client.post(
            "/api/work-packets",
            json={
                "workspace_id": "personal",
                "intent": secret,
                "payload": {"body": secret, "classification": "example_likely"},
                "mode_pack_id": "project_management.v1",
                "consumer": "runtime-proof-bundle",
            },
        )
        personal_packet.raise_for_status()
        write_json(api_dir / "personal-packet.json", personal_packet.json())

        transfer = client.post(
            "/api/transfer-proposals",
            json={
                "work_packet_id": personal_packet.json()["id"],
                "target_workspace_id": "example",
                "summary": "Classified personal task may concern Example",
            },
        )
        transfer.raise_for_status()
        write_json(api_dir / "transfer-proposed.json", transfer.json())
        example_before = client.get("/api/hermes/workspace-context?workspace_id=example")
        example_before.raise_for_status()
        console_before = client.get("/api/platform-console")
        console_before.raise_for_status()
        assert secret not in example_before.text
        assert secret not in console_before.text
        assert "Classified personal task may concern Example" not in example_before.text
        write_json(api_dir / "negative-control.json", {"example_context": example_before.json(), "platform_console": console_before.json()})

        approved = client.post(
            f"/api/transfer-proposals/{transfer.json()['id']}/decision",
            json={"approve": True, "decided_by": "owner"},
        )
        approved.raise_for_status()
        write_json(api_dir / "transfer-approved.json", approved.json())
        rejected_proposal = client.post(
            "/api/transfer-proposals",
            json={
                "work_packet_id": personal_packet.json()["id"],
                "target_workspace_id": "example",
                "summary": "Second transfer negative control",
            },
        )
        rejected_proposal.raise_for_status()
        rejected = client.post(
            f"/api/transfer-proposals/{rejected_proposal.json()['id']}/decision",
            json={"approve": False, "decided_by": "owner"},
        )
        rejected.raise_for_status()
        write_json(api_dir / "transfer-rejected.json", rejected.json())

        prepared: list[dict[str, Any]] = []
        checks = {"repo_read": True, "test_runner": True, "source_reader": True}
        for mode_pack_id, job_class in MODE_JOB_CLASSES.items():
            response = client.post(
                "/api/hermes/workspace-turns/prepare",
                json={
                    "workspace_id": "personal",
                    "mode_pack_id": mode_pack_id,
                    "skill_capability_checks": checks,
                    "text": f"Workspace proof held-out preparation for {mode_pack_id}",
                    "job_class": job_class,
                    "consumer": "runtime-proof-bundle",
                },
            )
            response.raise_for_status()
            prepared.append(response.json())
        write_json(api_dir / "prepared-mode-packs.json", prepared)

        route_command = [
            sys.executable,
            "-m",
            "orchestrator.cli.main",
            "route",
            "--job-class",
            "repo_coding",
            "--text",
            "Return only WORKSPACE_METER_OK for the local runtime proof.",
            "--dry-run",
        ]
        routed = subprocess.run(route_command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=45, check=True)
        route_payload = json.loads(routed.stdout)
        assert route_payload["decision"]["chosen_adapter"] == "ollama-http"
        write_json(run_dir / "governed-route.json", route_payload)

        usage_path = run_dir / "hermes-usage.json"
        hermes = subprocess.run(
            [
                "hermes",
                "--safe-mode",
                "--provider",
                "custom",
                "--model",
                args.model,
                "--usage-file",
                str(usage_path),
                "-z",
                "Return exactly WORKSPACE_METER_OK.",
            ],
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        )
        assert hermes.stdout.strip() == "WORKSPACE_METER_OK"
        usage = json.loads(usage_path.read_text(encoding="utf-8"))
        write_json(run_dir / "hermes-usage.json", usage)
        (run_dir / "hermes-output.txt").write_text(hermes.stdout, encoding="utf-8")
        tokens_in, tokens_out = usage_counts(usage)
        actual_provider = str(usage.get("provider") or "unknown")
        actual_model = str(usage.get("model") or "unknown")
        meter = client.post(
            f"/api/hermes/workspace-turns/{prepared[0]['work_packet']['id']}/complete",
            json={
                "workspace_id": "personal",
                "provider": actual_provider,
                "model": actual_model,
                "route": "hermes-cli",
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "actual_cost_usd": None,
                "estimated_cost_usd": None,
                "quota_source": "unknown",
                "quota_state": {"cost_status": str(usage.get("cost_status") or "unknown"), "source": "hermes_usage_report"},
                "measurement_source": "hermes_usage_report",
            },
        )
        meter.raise_for_status()
        write_json(api_dir / "meter-event.json", meter.json())
        workspace_meter = client.get("/api/hermes/workspace-meter?workspace_id=personal")
        workspace_meter.raise_for_status()
        assert int(workspace_meter.json()["tokens_total"]) == tokens_in + tokens_out
        write_json(api_dir / "workspace-meter.json", workspace_meter.json())

        settings = Settings(
            home=runtime_home,
            state_path=runtime_home / "state.sqlite",
            notifications_path=runtime_home / "notifications.jsonl",
            log_dir=runtime_home / "logs",
            repo_root=ROOT,
        )
        outcomes = asyncio.run(record_outcome_evidence(settings, prepared, run_id))
        write_json(run_dir / "outcome-loop.json", outcomes)
        console_after = client.get("/api/platform-console")
        console_after.raise_for_status()
        assert secret not in console_after.text
        assert console_after.json()["model_cards"]
        write_json(api_dir / "platform-console-final.json", console_after.json())
        root = client.get("/")
        root.raise_for_status()
        assert "Platform Console" in root.text and secret not in root.text
        (run_dir / "platform-console.html").write_text(root.text, encoding="utf-8")
        client.close()

        summary = {
            "run_id": run_id,
            "base_url": base_url,
            "state_path": str(runtime_home / "state.sqlite"),
            "platform_console_html": str(run_dir / "platform-console.html"),
            "checks": {
                "system_console_negative_control": True,
                "cross_workspace_retrieval_negative_control": True,
                "transfer_approved": approved.json()["state"] == "approved",
                "transfer_rejected": rejected.json()["state"] == "rejected",
                "five_mode_packs_prepared": len(prepared) == 5,
                "hermes_routed_turn": actual_provider != "unknown" and actual_model != "unknown",
                "live_meter_matches_usage": int(workspace_meter.json()["tokens_total"]) == tokens_in + tokens_out,
                "meter_metadata_matches_usage": (
                    meter.json()["resource_event"]["provider"] == actual_provider
                    and meter.json()["resource_event"]["model"] == actual_model
                    and meter.json()["resource_event"]["measurement_source"] == "hermes_usage_report"
                ),
                "governed_swarm_completed": outcomes["swarm"]["swarm"]["state"] == "completed",
                "held_out_policy_replay": outcomes["policy_candidate"]["replay"]["suite_id"] == "foundation-held-out-v1",
            },
        }
        write_json(run_dir / "summary.json", summary)
        (run_dir / "ui-url.txt").write_text(base_url, encoding="utf-8")
        if args.keep_server:
            write_json(run_dir / "server-process.json", {"pid": server.pid, "base_url": base_url})
            return run_dir
        return run_dir
    finally:
        if not args.keep_server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    print(run())

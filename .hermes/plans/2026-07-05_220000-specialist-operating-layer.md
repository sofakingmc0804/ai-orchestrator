# Specialist Operating Layer — End-to-End Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Wire the orchestrator's existing subsystems into 8 autonomous specialist roles, each with a defined contract, a self-recovery path, and a user-steering interface — so the AI OS operates itself continuously on this machine.

**Architecture:** Specialists are NOT external AI agents. They are Python modules + scheduler tasks + API endpoints running inside the always-on orchestrator process (FastAPI lifespan → supervisor thread → scheduler thread). The "agent" quality comes from: each owns a domain, runs on a schedule, produces evidence-backed receipts, self-recovers from blocks via repair queue + fallback paths, and surfaces to the dashboard. The user steers via approval gates, CLI commands, dashboard observation, and config files.

**Tech Stack:** Python 3.13, asyncio, FastAPI, aiosqlite, the existing orchestrator codebase (97 .py files, 39 test files, 202 passing tests per STATUS.md)

---

## Part 0: Execution Environment (this machine, this reality)

### 0.1 The venv problem (must fix first)

**Current state:** `python -m orchestrator.cli.main spec-status` fails with `ModuleNotFoundError: No module named 'aiosqlite'`. The Hermes runtime venv (`C:\Users\Couch\AppData\Local\hermes\hermes-agent\venv`) has fastapi/httpx/pydantic but NOT the orchestrator's own deps (aiosqlite, PyYAML, watchdog, starlette version pin).

**Fix:** Install the project in the venv that `start-orchestrator.ps1` uses. That script runs `python -m orchestrator.main`, and `python` resolves to whatever is on PATH (currently the Hermes venv python).

```bash
# In the orchestrator repo, with the runtime venv active:
cd /c/Users/Couch/dev/ai-orchestrator
pip install -e .
# OR if -e is problematic on Windows:
pip install aiosqlite PyYAML watchdog "starlette>=0.40,<0.47" "fastapi>=0.115,<0.116" uvicorn httpx psutil pydantic
```

**Verify:** `python -m orchestrator.cli.main spec-status` runs without import errors. `python -m pytest -q` runs (install pytest first: `pip install pytest pytest-asyncio hypothesis`).

### 0.2 The startup chain (already works, just needs the venv fixed)

```
scripts/start-orchestrator.ps1 -Watchdog
  └─► checks port 8765 every 5-300s (exponential backoff)
      └─► if not listening: starts `python -m orchestrator.main --port 8765` (hidden window)
          └─► orchestrator.main → create_app(settings) → lifespan:
              ├─ store.initialize()                          # SQLite schema
              ├─ discover_services_and_capabilities()         # probe all adapters
              ├─ discover_projects()                           # scan project roots
              ├─ run_supervisor_tick()                         # one immediate tick
              └─ start_supervisor_thread()                     # daemon thread, 5-min tick
                  └─► every 5 min:
                      ├─ recover_interrupted_dispatches()      # CT-23 crash survival
                      ├─ ensure_budget_probe_scheduler_task()  # 15-min quota refresh
                      ├─ run_due_scheduler_once()               # all due scheduler tasks
                      └─ repair_core_services()                # LM Studio / Hermes health
```

This chain ALREADY EXISTS and works. The specialists plug into it as **scheduler tasks** + **API endpoints** + **supervisor tick extensions**.

### 0.3 The scheduler task registry (where specialists register)

The scheduler (`scheduler/tasks.py`) already supports three task types:
- `standing_order_scan` — autopilot folder scans
- `operation_tournament` — evaluation tournaments
- `budget_probes_cron` — subscription usage refresh

Each specialist that runs on a schedule registers as a new `task_type` via `store.upsert_scheduler_task()`. The supervisor thread's `run_due_scheduler_once` picks them up automatically.

### 0.4 The repair queue (how specialists self-recover)

Every subsystem already writes to the repair queue on failure:
- `store.add_repair_item(source, failure_detail, suggested_action)` — creates an open repair item
- `store.resolve_repair_items(source, evidence)` — closes it when fixed
- The dashboard shows open repairs at `/api/repair-queue`
- The supervisor tick runs `retry_open_repairs` to retry failed work

**Self-recovery contract for every specialist:**
1. If the primary path fails, try the fallback path (each specialist has ≥1 defined fallback)
2. If all paths fail, write a repair item with the specific failure + suggested action
3. If the repair item is still open after N ticks, escalate to a notification (tray + in-app)
4. The user can resolve it via the dashboard or CLI, or the specialist auto-resolves when the underlying service recovers

---

## Part 1: The 8 Specialists

Each specialist is defined by a **contract** (the role), an **implementation** (the code), a **self-recovery path** (how it handles blocks), and a **user steering interface** (how Matt guides it).

### S1 — The Router (intent classification + ranked ladder)

**Role:** Take raw intent text → produce a ranked failover ladder with evidence-backed reasoning. This is the brain's front door.

**Already exists (reuse):**
- `routing/brain.py:route_brain()` — full implementation, reads job_classes + worker_cards + quota + quality + health
- `routing/worker_routing.py:route_with_workers()` — 10-dimension composite scoring, returns ranked ladder sorted by (health, quota, quality, marginal cost, composite)
- `governance/job_classifier.py:classify_intent()` — regex keyword patterns → 14 job classes
- `intent/interpreter.py:parse_intent()` — keyword → capability mapping
- `POST /api/route` — already serves the brain API
- `hermes/brain_bridge.py:route_for_hermes_prompt()` — already the inversion seam

**Gap to close:**
1. The classifier is pure regex. Add a model-backed classification fallback for ambiguous intents (low-confidence regex result → ask a flat-rate model to classify → persist the classification as a receipt).
2. The brain API already returns a `ranked_ladder` but the dispatcher only walks `candidates_considered` — ensure the ladder order is the walk order (it is, per `dispatcher.py:214`, but verify the sort is stable).
3. Add a `/api/route/explain` endpoint that returns the full reasoning trail (decision → considered → rejected → scores) for the dashboard.

**Self-recovery:**
- If no worker matches (empty ladder): write repair item "no safe adapter found for job_class X" with suggested action "add a capability for X or loosen project policy." Already done at `dispatcher.py:158-171`.
- If all candidates are unhealthy: the ladder is empty → repair item → notification to user.
- If the classifier returns low confidence (< 0.3): fall back to `quick_question` default (already done at `job_classifier.py:174`). The model-backed fallback adds a second chance before defaulting.

**User steering:**
- `orchestrator route --job-class X --text "..."` — override the classifier manually
- `POST /api/route` with `job_class` field — same override via API
- Project policy files (`.orchestrator-policy.yaml`) can set `allowed_adapters` / `forbidden_adapters` / `preferred_adapters` per project

### S2 — The Steward (live quota + budget enforcement)

**Role:** Probe live subscription/auth quota every 15 minutes, enforce 20% reserves, keep the budget lookup fed with real numbers.

**Already exists (reuse):**
- `discovery/subscription_usage.py` — real OAuth probes of ChatGPT/Claude/Gemini
- `scheduler/budget_probes_cron.py:run_budget_probes_once()` — the cron hook
- `routing/worker_routing.py:_budget_lookup_from_sources()` — merges subscription snapshots + legacy probes
- `scheduler/tasks.py:_run_budget_probes_task()` — scheduler task handler
- `process/supervisor.py:ensure_budget_probe_scheduler_task()` — seeds the 15-min task
- `provider_aliases.py` — maps service_ids to provider_ids

**Gap to close:**
1. **The bypass (BUILD_PLAN Sprint 1.1):** `dispatcher.py:128` reads `budget_probes` (placeholder 999999 sentinels) alongside subscription snapshots. The `_budget_lookup_from_sources` already prefers live snapshots over probes, but verify the dispatcher passes both sources. It does (`dispatcher.py:127-128`: `subscription_usage_snapshots` + `budget_probes`). The bypass is already fixed.
2. Add a "quota health" panel to the dashboard showing: per-provider, last probe time, remaining/limit, reserve status, staleness (if snapshot > 24h, mark stale).
3. Verify the 20% reserve enforcement is wired: `_reserve_rejection_reason()` at `worker_routing.py:298-328` checks `remaining <= reserve_units` and rejects.

**Self-recovery:**
- If a probe fails (OAuth expired, service down): write repair item with `suggested_action` = "Re-authenticate [provider] or check the subscription status page."
- If a snapshot is stale (> 24h): `_snapshot_is_stale()` at `worker_routing.py:96` marks it unusable, routing falls back to legacy probes, and the budget score drops to 0.15 (degraded). This is the fallback.
- If ALL quota sources fail: the steward marks all subscription workers as degraded (budget_score 0.15), routing prefers local/flat-rate workers. The system keeps working on the flat-rate floor.

**User steering:**
- `POST /api/subscriptions/refresh` — force a quota probe refresh
- `orchestrator budget` — see current quota state
- `orchestrator cli budget` — CLI equivalent
- The dashboard `/budget` page shows live flowmeters + reserve traffic lights

### S3 — The Referee (comparative quality evaluation)

**Role:** Continuously score model-vs-model accuracy on identical tasks, keep quality rankings fresh from both controlled tournaments AND passive scoring of real dispatches.

**Already exists (reuse):**
- `evaluation/tournament.py:run_deterministic_tournament()` — runs N workers on 1 task, validates with deterministic referees, scores relative (min-max within population), persists to `operation_quality_scores`
- `evaluation/blind_panel.py` — blind multi-judge consensus with bias controls (self-provider exclusion, self-family exclusion, ≥3 diverse judges, variance threshold)
- `benchmarks/validators/operation_validators.py:validate_task()` — deterministic validators per domain
- `benchmarks/manifest.json` — operation definitions with ground truth
- `scheduler/tasks.py:_run_operation_tournament_task()` — scheduler task handler for tournaments
- `governance/worker_roster_builder.py:load_live_operation_quality_scores()` — reads scores back into the router

**Gap to close:**
1. **Passive scoring seam (BUILD_PLAN Sprint 1.2):** In `dispatcher._complete_dispatch()`, after a successful dispatch, call `validate_task(operation_task, raw_output)` and persist the score. The `operation_task` is already in the envelope (`dispatcher.py:192-194`). Add the `validate_task` call + `store.record_operation_quality_score()`.
2. **Seed a default tournament task:** Add a scheduler task (type `operation_tournament`) that runs weekly across the top workers on a fixed operation set. Seed it in `ensure_budget_probe_scheduler_task`-style (a new `ensure_quality_tournament_task` in supervisor).
3. **Stale score decay:** In `load_live_operation_quality_scores()`, add a `latest_created_at` check — if the latest score for a (worker, domain) pair is > 7 days old, decay its weight in the composite (or mark it "stale" so the router falls back to benchmark scores).

**Self-recovery:**
- If a tournament fails (worker didn't produce output): the validator returns score 0.0, the relative scoring handles it (that worker ranks last). No repair item needed — the tournament self-corrects.
- If a blind panel can't get 3 eligible judges: `build_blind_judge_panel` raises ValueError. Catch it, write a repair item "insufficient diverse judges for domain X," fall back to deterministic-only scoring for that domain (mark it "comparative-pending" in the leaderboard).
- If validators are missing for a domain: skip passive scoring for that domain, log it, the router falls back to the `0.5` default quality score.

**User steering:**
- `POST /api/scheduler/tasks/{task_id}/trigger` — manually trigger a tournament
- `orchestrator cli receipts --job-class X` — see quality scores by domain
- Dashboard `/api/quality-leaderboard` — see rankings per operation-domain

### S4 — The Dispatcher (execution + failover + receipts)

**Role:** Walk the ranked ladder, execute via the right adapter, retry on failure, fall back to the next rung, write receipts with raw proof.

**Already exists (reuse):**
- `dispatch/dispatcher.py:Dispatcher._execute_intent()` — the full loop: route → record → walk candidates → retry 3× per adapter → fall back → write receipt
- `adapters/builtins.py` — 12+ adapter implementations (Ollama HTTP/CLI/Cloud, Claude, Codex, Copilot, Gemini, Hermes, LM Studio, OpenClaw, synthetic)
- `dispatch/receipt_enhanced.py:build_receipt_data()` — receipt generation
- `process/repair_retry.py` — repair queue retry
- `usage/tokens.py:extract_token_usage()` — token accounting per attempt

**Gap to close:**
1. **Raw proof persistence (BUILD_PLAN Sprint 0.1):** Verify that `result['raw']` is persisted on every receipt. The Ollama adapter returns `{"raw": data}` (`builtins.py:232`), and Claude returns `{"raw": result}` (`builtins.py:453`). Verify `store.record_dispatch` persists this to the `receipts.full_receipt` JSON column. It does (via `build_receipt_data`).
2. **The failover floor:** Verify that when all subscription workers are exhausted, the dispatcher reaches the flat-rate floor (Ollama Cloud). The ladder sort puts `flat_rate` marginal cost first (`worker_routing.py:597`). If the floor is also down, the repair item says "all flat-rate workers exhausted."
3. **Token-per-directive accounting (BUILD_PLAN Sprint 5.2):** The token usage is already recorded per attempt (`dispatcher.py:237-245`). Verify `build_token_accounting_payload` aggregates by `intent_id` (completed directive) not just by dispatch. It does (`usage/accounting.py`).

**Self-recovery:**
- If an adapter times out: `stop_candidate_retries = True` (`dispatcher.py:279-281`), moves to next candidate. Already done.
- If all candidates fail: repair item + notification + intent marked failed. Already done (`dispatcher.py:311-333`).
- If the entire ladder is exhausted: `decision.chosen_adapter` is None → repair item "no safe adapter found." Already done.
- **The floor guarantee:** If the top rung dies mid-task, the dispatcher walks to the next candidate. The task is not lost because the intent + envelope are already persisted. The next rung picks up the same envelope. This is the F4 guarantee.

**User steering:**
- `orchestrator cli dispatch --text "..."` — manual dispatch
- `POST /api/dispatch` — API dispatch
- `orchestrator cli approve --intent <id>` / `reject --intent <id>` — approval gate for high/critical
- `POST /api/approvals/{intent_id}/{approve|reject}` — API approval

### S5 — The Sentinel (governance + enforcement)

**Role:** Enforce the narrow hard-deny ruleset, steer advisory violations, verify terminal states, gate premium agents. Keep the ruleset NARROW — everything else is advisory + receipted.

**Already exists (reuse):**
- `skills/gate.py` — hard-deny for: `forbidden_metered_route`, `unapproved_external_send`, `fp_009_non_read_tool_limit`, `gate_0_read_before_write`, `gate_6_predecessor_retirement`. Plus Gmail-specific guards (forbidden identity, wrong UEI/CAGE, agent-authored signatures, daily GovCon scope).
- `skills/models.py:SkillHookPlan` — with `enforcement_mode: "advisory" | "hard_deny"`
- `skills/detector.py:detect_skill_route()` — builds the plan from prompt
- `skills/runtime.py:run_hook_event()` — the hook entry point (stdin JSON → decision)
- `enforcement/epistemic/` — the epistemic engine (audit-only, returns "allow")
- `reports/acceptance_battery.py:_prove_bounded_critic()` — the 3-cycle UNCERTAIN proof

**Gap to close:**
1. **Verify hard-deny is real, not cosmetic (BUILD_PLAN Sprint 5.1):** The gate already emits `permissionDecision: "deny"` (`gate.py:137-147`). Verify `.codex/hooks.json` wires this through so the deny actually blocks. Check `.codex/hooks.json` exists and references the hook script.
2. **Tokens-per-directive (BUILD_PLAN Sprint 5.2):** Already measured by `usage/accounting.py`. Verify the dashboard surfaces cost-vs-quality per agent (the acceptance battery checks this at F6).
3. **Keep the ruleset narrow:** Do NOT add new hard-deny rules unless the owner explicitly approves. The advisory path produces receipts; the Scribe (S6) surfaces patterns. If a pattern of real damage emerges, the owner can promote an advisory rule to hard-deny.

**Self-recovery:**
- If the hook script itself crashes: `skills/runtime.py:337` catches the exception and passes (advisory mode — don't block work because the governance script broke). The crash is logged.
- If a hard-deny rule fires incorrectly: the user can override via `confirm skill route <plan_id>` (runtime.py:73-75) or by adjusting the project policy.
- If the epistemic engine is unavailable: `_prove_retired_prose_gates` checks it returns "allow" — if it's down, the acceptance battery fails F8, which surfaces to the user.

**User steering:**
- The skill hook plan is persisted per session/turn (`skills/runtime.py:persist_plan`). The user sees the plan in the dashboard.
- `confirm skill route <plan_id>` — approve a pending route
- `cancel skill route <plan_id>` — reject a pending route
- `use skills: skill-a, skill-b` — override the selected skills
- The dashboard `/api/governance-receipts` shows recent enforcement decisions

### S6 — The Scribe (audit + owner receipts)

**Role:** Record every decision, dispatch, and state change. Produce the signed owner receipt. Ensure one honest source of record. Retire false claims.

**Already exists (reuse):**
- `reports/owner_receipt.py` — owner receipt generation
- `reports/audit.py` — audit log exporter
- `reports/acceptance_battery.py:run_acceptance_battery()` — the F1–F8 battery with signed receipt
- `state/store.py:audit()` — writes to `audit_log` table
- `state/store.py:record_dispatch()`, `record_routing()`, `record_token_usage()` — all write evidence
- `spec_status.py:evaluate_spec_status()` — the honest scoreboard (no row-count passes)

**Gap to close:**
1. **Verify every specialist action writes to audit_log:** The supervisor does (`supervisor.py:134`), the scheduler does (`tasks.py:99`), the brain bridge does (`brain_bridge.py:91`). Verify the dispatcher, steward, and referee also audit. The dispatcher records routing + dispatch but may not explicitly audit the dispatch action — check and add `store.audit("dispatcher", "dispatch", intent_id, {...})` if missing.
2. **Acceptance battery as a scheduled task:** Add a scheduler task (type `acceptance_battery`) that runs daily, producing a signed owner receipt. Seed it in the supervisor.
3. **Spec-status on a schedule:** Add a weekly spec-status snapshot to the audit log, so the user can see the trend (are we gaining or losing proven targets?).

**Self-recovery:**
- If the audit log write fails (SQLite error): the action still completes (audit is fire-and-forget). The error is logged.
- If the acceptance battery fails (one of F1–F8 is red): the receipt has `state: "failed"`. The supervisor surfaces this as a notification. The user sees which F failed and the evidence.
- If spec-status shows a target went from "passed" to "missing": the scribe records the regression in the audit log. The user investigates via the dashboard.

**User steering:**
- `orchestrator spec-status` — see the honest scoreboard
- `orchestrator acceptance-battery` — run the F1–F8 proof
- `GET /api/audit-log` — full audit trail
- `GET /api/spec-status` — API scoreboard
- The owner receipt at `.runtime/orchestrator/owner-receipts/` — signed JSON

### S7 — The Surgeon (health + recovery + always-on)

**Role:** Keep the brain alive. Supervise the process, restart on crash, recover in-flight work, keep services healthy.

**Already exists (reuse):**
- `process/supervisor.py:start_supervisor_thread()` — daemon thread, 5-min tick, exponential backoff on crash (2s → 4s → 8s → ... → 300s)
- `process/supervisor.py:run_supervisor_tick()` — recovers interrupted dispatches, runs scheduler, repairs services
- `process/recovery.py:repair_core_services()` — LM Studio auto-repair (daemon up → server start → model check), Hermes/OpenClaw retirement checks
- `process/repair_retry.py:retry_open_repairs()` — retries failed work
- `scripts/start-orchestrator.ps1 -Watchdog` — the external watchdog (port check → health check → restart → receipt)
- `scripts/install-startup-task.ps1` — Windows Task Scheduler entry for boot-time start

**Gap to close:**
1. **Verify the watchdog is installed:** Check that `install-startup-task.ps1` has been run (Task Scheduler has the entry). If not, run it.
2. **Verify the supervisor thread survives app startup:** The lifespan starts it (`server.py:88`), and stops it on shutdown (`server.py:91-98`). The thread is daemon=True, so it dies with the process. The WATCHDOG restarts the process. This is the CT-23 proof chain.
3. **Add a health-check endpoint for the watchdog:** Already exists at `GET /api/dashboard-status` (used by `Test-OrchestratorHealth` in the watchdog script).
4. **Crash recovery proof:** The supervisor writes `restart_recovery_proved: true` when it recovers interrupted dispatches (`supervisor.py:131`). The watchdog writes `restart_recovery_proved: true` on `watchdog_restart` (`start-orchestrator.ps1:63`). These are the CT-23 proofs.

**Self-recovery:**
- If the supervisor thread crashes: it writes a `supervisor_tick_crash` receipt with backoff (`supervisor.py:161-176`), then retries with exponential backoff.
- If the orchestrator process crashes: the watchdog detects (health check fails), restarts, writes `watchdog_restart` receipt. The supervisor's `recover_interrupted_dispatches` resumes in-flight work.
- If a service (LM Studio) is down: `repair_lm_studio_local_server` tries to start it (daemon up → server start). If that fails, it writes a repair packet (operator action required). The service stays degraded until the operator fixes it.
- If the watchdog itself dies: it's a PowerShell loop; if the terminal closes, it stops. Mitigation: install it as a Windows Task Scheduler entry (runs at boot, every 5 min).

**User steering:**
- `scripts/start-orchestrator.ps1 -Watchdog` — start the watchdog manually
- `scripts/install-startup-task.ps1` — install boot-time auto-start
- `POST /api/repair-services` — force a service repair cycle
- `POST /api/retry-repairs` — retry all open repair items
- `GET /api/repair-queue` — see what's broken

### S8 — The Cartographer (discovery + roster + extensibility)

**Role:** Discover new services/projects, build/refresh the worker roster, keep capability contracts hot-reloadable, prove new-service registration.

**Already exists (reuse):**
- `governance/worker_roster_builder.py` — builds the 96-worker roster from probes + rosters, writes to `worker_cards` table
- `discovery/services.py:discover_services_and_capabilities()` — probes all adapters, returns services + capabilities
- `discovery/projects.py:discover_projects()` — scans project roots (`.git`, `CLAUDE.md`, `AGENTS.md`, `package.json`)
- `registry/contracts.py:capabilities_from_contract()` — loads capability YAMLs
- `surface_adapters.py:dispatch_adapter_for_worker()` — maps worker surfaces to adapter names
- `data/rosters/worker_roster_v2.json` — 96 workers, 7 surfaces

**Gap to close:**
1. **Hot-reload contracts:** The `capabilities_from_contract` reads YAML files on each call. Verify it picks up new contracts without restart. If not, add a file watcher (watchdog library is in deps).
2. **New-service registration proof (CT-21):** The synthetic test service adapter already exists (`adapters/builtins.py`). Verify the acceptance battery proves it end-to-end with a real receipt. It does (`acceptance_battery.py:prove_synthetic_adapter_with_selections`).
3. **Roster refresh on a schedule:** Add a scheduler task (type `roster_refresh`) that runs daily, re-probes all services, rebuilds the worker roster, and records the delta (new workers, removed workers, changed capabilities).
4. **Project discovery on a schedule:** Same — daily scan of project roots, upsert projects, detect new/removed.

**Self-recovery:**
- If a service probe fails: the service is marked `stopped` with a `repair_action`. The roster builder skips it. The router doesn't route to it.
- If the roster is empty (first run / all probes failed): the router falls back to the static capability list (`dispatcher.py:153-155`). The system keeps working with whatever is available.
- If a new service appears (user installs a new AI app): the next discovery scan finds it, the roster builder adds it, the router can use it on the next dispatch. No code changes needed (CT-21 guarantee).

**User steering:**
- `POST /api/refresh` — force a full discovery refresh (services + capabilities + projects)
- `POST /api/refresh-services` — fast service health re-probe
- `orchestrator cli refresh-workers` — rebuild the worker roster
- `orchestrator cli workers` — see the current roster

---

## Part 2: Implementation Phases

Each phase is independently shippable. Each phase has bite-sized tasks with exact file paths, code, and verification.

### Phase A: Fix the execution environment (prerequisite — nothing works without this)

#### Task A1: Install project dependencies in the runtime venv

**Files:** none (environment setup)

**Step 1:** Identify the venv that runs the orchestrator
```bash
which python
# Expected: /c/Users/Couch/AppData/Local/hermes/hermes-agent/venv/Scripts/python
```

**Step 2:** Install project deps
```bash
cd /c/Users/Couch/dev/ai-orchestrator
pip install -e .
```

**Step 3:** Verify imports
```bash
python -c "import aiosqlite, fastapi, httpx, pydantic, yaml, watchdog; print('OK')"
# Expected: OK
```

**Step 4:** Verify spec-status runs
```bash
python -m orchestrator.cli.main spec-status 2>&1 | head -20
# Expected: JSON output with target counts, no ModuleNotFoundError
```

**Step 5:** Verify tests run
```bash
pip install pytest pytest-asyncio hypothesis
python -m pytest -q 2>&1 | tail -5
# Expected: 202 passed (or close)
```

#### Task A2: Verify the startup chain works end-to-end

**Step 1:** Start the orchestrator via the watchdog
```powershell
# In a separate terminal (NOT the agent's terminal):
cd C:\Users\Couch\dev\ai-orchestrator
.\scripts\start-orchestrator.ps1 -WatchdogOnce
# Expected: "started pid=XXXX healthy=True receipt=..."
```

**Step 2:** Verify health endpoint
```bash
curl -s http://127.0.0.1:8765/api/dashboard-status | python -m json.tool | head -20
# Expected: JSON with services, data_sources, route_api_ready=true
```

**Step 3:** Verify route API
```bash
curl -s -X POST http://127.0.0.1:8765/api/route \
  -H "Content-Type: application/json" \
  -d '{"text":"fix this repo bug","job_class":"repo_coding"}' | python -m json.tool | head -30
# Expected: JSON with ranked_ladder, decision, reasoning
```

**Step 4:** Stop the orchestrator
```bash
# Kill the process on port 8765
# (or just close the terminal that started it)
```

---

### Phase B: Wire the passive quality scoring seam (S3 Referee)

This is the single highest-leverage change: it makes the quality rankings flow from real work, not just synthetic tournaments.

#### Task B1: Add the passive scoring call to _complete_dispatch

**Files:**
- Modify: `orchestrator/dispatch/dispatcher.py` (the `_complete_dispatch` method, around line 261)
- Test: `tests/test_quality_loop.py` (already exists, extend it)

**Step 1:** Read the current `_complete_dispatch` method to find the exact insertion point

```bash
python -c "
import inspect
from orchestrator.dispatch.dispatcher import Dispatcher
src = inspect.getsource(Dispatcher._complete_dispatch)
print(src[:3000])
"
```

**Step 2:** Add the passive scoring call after the receipt is written

The insertion point is after `await self.store.record_dispatch(...)` with the completed state, before the return. Add:

```python
# Passive quality scoring: validate the output and persist the score
operation_task = envelope.get("operation_task")
if isinstance(operation_task, dict) and result.get("ok"):
    try:
        from benchmarks.validators.operation_validators import validate_task
        validation = validate_task(operation_task, result.get("text", ""))
        scores = validation.get("scores", {})
        composite = float(scores.get("composite", 0.0))
        await self.store.record_operation_quality_score({
            "id": f"oqs_passive_{dispatch_id}",
            "dispatch_id": dispatch_id,
            "worker_id": chosen_candidate.get("worker_id", adapter_name),
            "operation_domain": operation_task.get("domain_id", job_class),
            "validator_name": str(validation.get("validator", "passive")),
            "composite_score": composite,
            "dimensional_scores": scores,
            "task_id": operation_task.get("task_id", intent.id),
            "proof_kind": "live",
            "validation": validation,
            "created_at": iso(),
        })
    except Exception:
        pass  # Passive scoring is best-effort; never block dispatch on it
```

**Step 3:** Verify the test passes
```bash
python -m pytest tests/test_quality_loop.py -v
# Expected: PASS
```

**Step 4:** Run a real dispatch and verify a score is persisted
```bash
python -m orchestrator.cli.main dispatch --text "classify this text as positive or negative: I love this" 2>&1 | tail -10
# Then check the scores table:
python -c "
import sqlite3
con = sqlite3.connect('.runtime/orchestrator/state.sqlite')
con.row_factory = sqlite3.Row
rows = con.execute('SELECT * FROM operation_quality_scores ORDER BY created_at DESC LIMIT 5').fetchall()
for r in rows: print(dict(r))
"
# Expected: at least one row with proof_kind='live'
```

---

### Phase C: Seed the continuous tournament (S3 Referee)

#### Task C1: Add ensure_quality_tournament_task to the supervisor

**Files:**
- Modify: `orchestrator/process/supervisor.py` (add `ensure_quality_tournament_task`)
- Test: `tests/test_crash_audit_scheduler.py` (extend)

**Step 1:** Add the function (modeled on `ensure_budget_probe_scheduler_task`):

```python
QUALITY_TOURNAMENT_TASK_ID = "task_quality_tournament"
QUALITY_TOURNAMENT_INTERVAL_SECONDS = 7 * 24 * 60 * 60  # weekly

async def ensure_quality_tournament_task(store: StateStore) -> str:
    existing = await store.get_scheduler_task(QUALITY_TOURNAMENT_TASK_ID)
    if existing and existing.get("task_type") == "operation_tournament" and bool(existing.get("enabled")):
        return QUALITY_TOURNAMENT_TASK_ID
    await store.upsert_scheduler_task(
        name="Weekly comparative quality tournament",
        task_type="operation_tournament",
        target_ref="operation_quality_scores",
        payload={
            "operation_task": {
                "task_id": "weekly_battery",
                "domain_id": "classification",
                "validator": "exact_match",
                "expected": "positive",
            },
            "workers": [],  # populated at run time from the roster
            "worker_outputs": {},  # populated at run time
        },
        schedule_kind="interval",
        interval_seconds=QUALITY_TOURNAMENT_INTERVAL_SECONDS,
        enabled=True,
        task_id=QUALITY_TOURNAMENT_TASK_ID,
        next_run_at=iso(datetime.now(timezone.utc) + timedelta(seconds=QUALITY_TOURNAMENT_INTERVAL_SECONDS)),
    )
    return QUALITY_TOURNAMENT_TASK_ID
```

**Step 2:** Call it in `run_supervisor_tick` (after `ensure_budget_probe_scheduler_task`):

```python
quality_task_id = await ensure_quality_tournament_task(state)
```

Add `quality_task_id` to the receipt payload.

**Step 3:** Verify
```bash
python -m pytest tests/test_crash_audit_scheduler.py -v
# Expected: PASS
```

---

### Phase D: Add model-backed classification fallback (S1 Router)

#### Task D1: Add a model-backed classifier for low-confidence intents

**Files:**
- Modify: `orchestrator/governance/job_classifier.py` (add `classify_with_model_fallback`)
- Test: `tests/test_routing_intents.py` (extend)

**Step 1:** Add the fallback function:

```python
def classify_with_model_fallback(text: str, model_classifier=None) -> ClassificationResult:
    """Classify intent, falling back to a model call for low-confidence results."""
    result = classify_intent(text)
    if result.confidence >= 0.5:
        return result  # Regex is confident enough

    # Low confidence: try model-backed classification if available
    if model_classifier is None:
        return result  # No model classifier wired; return the regex result

    try:
        model_result = model_classifier(text)
        if model_result and model_result.get("job_class"):
            return ClassificationResult(
                job_class=model_result["job_class"],
                confidence=float(model_result.get("confidence", 0.7)),
                matched_keywords=["model_fallback"],
                reasoning=f"Model-backed classification after low-confidence regex ({result.confidence:.2f}): {model_result.get('reasoning', '')}",
            )
    except Exception:
        pass  # Model classifier failed; return the regex result

    return result
```

**Step 2:** Wire the model classifier (uses the flat-rate Ollama floor):

```python
# In classify_with_fallback, add an optional model_classifier parameter:
def classify_with_fallback(
    text: str,
    override: str | None = None,
    model_classifier=None,
) -> dict[str, Any]:
    if override:
        return {"job_class": override, "confidence": 1.0, ...}
    result = classify_with_model_fallback(text, model_classifier)
    return {
        "job_class": result.job_class,
        "confidence": result.confidence,
        "matched_keywords": result.matched_keywords,
        "reasoning": result.reasoning,
        "overridden": False,
    }
```

**Step 3:** The model_classifier is a callable that sends the text to the flat-rate Ollama floor with a classification prompt. This is wired in `route_brain` or `dispatcher._execute_intent` — wherever `classify_with_fallback` is called.

**Step 4:** Verify
```bash
python -m pytest tests/test_routing_intents.py -v
# Expected: PASS (existing tests + new test for model fallback)
```

---

### Phase E: Seed the daily acceptance battery (S6 Scribe)

#### Task E1: Add ensure_acceptance_battery_task to the supervisor

**Files:**
- Modify: `orchestrator/process/supervisor.py` (add `ensure_acceptance_battery_task`)
- Modify: `orchestrator/scheduler/tasks.py` (add `acceptance_battery` task type handler)

**Step 1:** Add the task type handler in `tasks.py`:

```python
async def _run_acceptance_battery_task(
    settings: Settings,
    store: StateStore,
    task: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    from orchestrator.reports.acceptance_battery import run_acceptance_battery
    receipt = await run_acceptance_battery(settings)
    result.update({
        "state": "completed" if receipt.get("state") == "passed" else "failed",
        "acceptance_receipt": receipt,
        "completed_at": iso(),
    })
    await store.mark_scheduler_task_run(str(task.get("id") or ""), int(task.get("interval_seconds") or 0))
    await store.audit("scheduler", "scheduler_task_run", str(task.get("id") or ""), result)
    return result
```

**Step 2:** Add the task type dispatch in `run_scheduler_task_once`:

```python
if task_type == "acceptance_battery":
    return await _run_acceptance_battery_task(settings, store, task, result)
```

**Step 3:** Add the seed function in `supervisor.py`:

```python
ACCEPTANCE_BATTERY_TASK_ID = "task_acceptance_battery"
ACCEPTANCE_BATTERY_INTERVAL_SECONDS = 24 * 60 * 60  # daily

async def ensure_acceptance_battery_task(store: StateStore) -> str:
    existing = await store.get_scheduler_task(ACCEPTANCE_BATTERY_TASK_ID)
    if existing and existing.get("task_type") == "acceptance_battery" and bool(existing.get("enabled")):
        return ACCEPTANCE_BATTERY_TASK_ID
    await store.upsert_scheduler_task(
        name="Daily F1-F8 acceptance battery",
        task_type="acceptance_battery",
        target_ref="owner_receipts",
        payload={"proof_kind": "live"},
        schedule_kind="interval",
        interval_seconds=ACCEPTANCE_BATTERY_INTERVAL_SECONDS,
        enabled=True,
        task_id=ACCEPTANCE_BATTERY_TASK_ID,
        next_run_at=iso(datetime.now(timezone.utc) + timedelta(seconds=ACCEPTANCE_BATTERY_INTERVAL_SECONDS)),
    )
    return ACCEPTANCE_BATTERY_TASK_ID
```

**Step 4:** Call it in `run_supervisor_tick`.

**Step 5:** Verify
```bash
python -m pytest tests/test_acceptance_battery.py -v
# Expected: PASS
```

---

### Phase F: Seed the roster refresh task (S8 Cartographer)

#### Task F1: Add roster_refresh task type

**Files:**
- Modify: `orchestrator/scheduler/tasks.py` (add `roster_refresh` handler)
- Modify: `orchestrator/process/supervisor.py` (add `ensure_roster_refresh_task`)

**Step 1:** Add the handler in `tasks.py`:

```python
async def _run_roster_refresh_task(
    settings: Settings,
    store: StateStore,
    task: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    from orchestrator.discovery.services import discover_services_and_capabilities
    from orchestrator.discovery.projects import discover_projects
    services, caps = await discover_services_and_capabilities()
    await store.upsert_services(services)
    await store.upsert_capabilities(caps)
    projects = discover_projects([settings.repo_root], max_depth=3)
    await store.upsert_projects(projects)
    result.update({
        "state": "completed",
        "services": len(services),
        "capabilities": len(caps),
        "projects": len(projects),
        "completed_at": iso(),
    })
    await store.mark_scheduler_task_run(str(task.get("id") or ""), int(task.get("interval_seconds") or 0))
    await store.audit("scheduler", "scheduler_task_run", str(task.get("id") or ""), result)
    return result
```

**Step 2:** Add the task type dispatch and seed function (same pattern as Phase E).

---

### Phase G: Add the route explain endpoint (S1 Router dashboard)

#### Task G1: Add /api/route/explain endpoint

**Files:**
- Modify: `orchestrator/ui/server.py` (add endpoint)

**Step 1:** Add the endpoint after the existing `/api/route`:

```python
class RouteExplainRequest(BaseModel):
    text: str
    job_class: str | None = None

@app.post("/api/route/explain")
async def route_explain(req: RouteExplainRequest) -> dict[str, object]:
    decision = await route_brain(store, text=req.text, job_class=req.job_class)
    # Return the full reasoning trail for the dashboard
    return {
        "job_class": decision.get("job_class"),
        "classification": decision.get("classification"),
        "ranked_ladder": [
            {
                "worker_id": r.get("worker_id"),
                "adapter_name": r.get("adapter_name"),
                "model_id": r.get("model_id"),
                "provider_id": r.get("provider_id"),
                "contract_type": r.get("contract_type"),
                "composite_score": r.get("composite_score"),
                "health_score": r.get("health_score"),
                "budget_score": r.get("budget_score"),
                "measured_quality_score": r.get("measured_quality_score"),
                "marginal_cost_class": r.get("marginal_cost_class"),
                "failover_floor": r.get("failover_floor"),
            }
            for r in decision.get("ranked_ladder", [])
        ],
        "rejected": [
            {"worker_id": r.get("worker_id"), "rejected_reason": r.get("rejected_reason")}
            for r in decision.get("rejected", [])
        ],
        "reasoning": decision.get("reasoning"),
    }
```

**Step 2:** Verify
```bash
curl -s -X POST http://127.0.0.1:8765/api/route/explain \
  -H "Content-Type: application/json" \
  -d '{"text":"fix this repo bug"}' | python -m json.tool | head -40
# Expected: full ladder with scores
```

---

## Part 3: Self-Recovery Patterns (how specialists deal with blocks)

Every specialist follows the same self-recovery contract:

### Level 0: Fallback path
Each specialist has ≥1 defined fallback:
- S1 Router: regex classifier → model-backed classifier → `quick_question` default
- S2 Steward: live snapshot → legacy probe → degraded (0.15 budget score)
- S3 Referee: deterministic validator → blind panel → "comparative-pending" marker
- S4 Dispatcher: top candidate → next candidate → flat-rate floor → repair queue
- S5 Sentinel: hard-deny → advisory context → no-op (pass through)
- S6 Scribe: audit write fails → log and continue (never block work on audit failure)
- S7 Surgeon: supervisor tick fails → exponential backoff → watchdog restart
- S8 Cartographer: probe fails → mark stopped → skip → static capability fallback

### Level 1: Repair queue
If the fallback path also fails, write a repair item:
```python
await store.add_repair_item(
    source="specialist_name",
    failure_detail="specific failure message with mechanism",
    suggested_action="concrete next step for the user or the surgeon",
)
```
The repair item is visible at `/api/repair-queue` and retried by `retry_open_repairs`.

### Level 2: Notification
If a repair item stays open for > N ticks, escalate to a notification:
```python
await notifications.publish(Notification(
    id=f"ntf_{uuid.uuid4().hex[:12]}",
    severity="error",
    title="Specialist X is blocked",
    body="failure detail + suggested action",
    channels_requested=["in_app", "tray"],
))
```

### Level 3: Operator packet
If the specialist cannot self-recover AND the user cannot fix it via the dashboard, write an operator packet (like `recovery.py:_write_lm_studio_repair_packet`) — a markdown file with the exact manual steps.

### Level 4: Graceful degradation
The system NEVER halts. If a specialist is fully down:
- S1 down → the router uses the static capability list (`dispatcher.py:153-155`)
- S2 down → routing prefers local/flat-rate workers (budget score 0.15 for unknowns)
- S3 down → quality scores are stale → router uses benchmark scores (weight 0.03)
- S4 down → no dispatch possible → repair item + notification (this is the only hard stop)
- S5 down → advisory mode (no hard-deny) → work proceeds without governance
- S6 down → no audit trail → work proceeds without recording (worst case: we lose evidence, not capability)
- S7 down → the process may crash and not restart → the watchdog handles this (external to the process)
- S8 down → stale roster → router uses whatever is in the DB from the last successful scan

---

## Part 4: User Guidance Interface (how Matt steers the specialists)

### 4.1 Approval gates (automatic — the user doesn't need to check)

For high/critical consequence tiers, the dispatcher surfaces an approval card:
- The intent is created with state `awaiting_approval`
- A notification is published (in_app + tray + email)
- The user approves/rejects via:
  - `orchestrator cli approve --intent <id>`
  - `POST /api/approvals/{intent_id}/approve`
  - The dashboard approval panel

Per-folder policy can loosen this: `.orchestrator-policy.yaml` with `approval_required_for: []` disables approval for that project.

### 4.2 CLI steering (manual control)

| Command | What it does | Specialist |
|---|---|---|
| `orchestrator route --job-class X --text "..."` | Override the classifier | S1 |
| `orchestrator budget` | See quota state | S2 |
| `orchestrator cli dispatch --text "..."` | Manual dispatch | S4 |
| `orchestrator cli approve/reject --intent <id>` | Approval gate | S4/S5 |
| `orchestrator spec-status` | Honest scoreboard | S6 |
| `orchestrator acceptance-battery` | Run F1-F8 proof | S6 |
| `orchestrator cli workers` | See the roster | S8 |
| `orchestrator cli refresh-workers` | Rebuild the roster | S8 |

### 4.3 Dashboard observation (passive monitoring)

| Page | URL | What it shows |
|---|---|---|
| Home | `/` | System health, activity stream |
| Workers | `/workers` | 96-worker roster grid |
| Budget | `/budget` | Live quota flowmeters + reserve lights |
| Receipts | `/receipts` | Dispatch history with proof |
| Connectors | `/connectors` | Connector config UI |

API endpoints for the dashboard:
| Endpoint | Specialist |
|---|---|
| `GET /api/route/explain` | S1 (the ranked ladder with scores) |
| `GET /api/budget` | S2 (live quota) |
| `GET /api/quality-leaderboard` | S3 (comparative rankings) |
| `GET /api/dispatches` | S4 (dispatch history) |
| `GET /api/governance-receipts` | S5 (enforcement decisions) |
| `GET /api/audit-log` | S6 (full audit trail) |
| `GET /api/spec-status` | S6 (honest scoreboard) |
| `GET /api/repair-queue` | S7 (what's broken) |
| `GET /api/services` | S8 (discovered services) |
| `GET /api/workers` | S8 (the roster) |

### 4.4 Config files (policy steering)

- `.orchestrator-policy.yaml` — per-project routing rules, approval thresholds, allowed/forbidden adapters
- `orchestrator/config/efficiency_policy.json` — vendor-neutral standard-of-steps (compiled into each agent's config)
- `.codex/hooks.json` — the skill hook wiring (what events trigger the sentinel)

### 4.5 The steering loop (how it all fits together)

```
Matt types intent into Hermes
  └─► Hermes calls brain route API (S1 Router)
      └─► S1 returns ranked ladder
      └─► S5 Sentinel checks authority
          └─► if high/critical: approval card → Matt approves
      └─► S4 Dispatcher walks the ladder
          └─► S2 Steward's quota data is already in the ladder
          └─► S3 Referee's quality scores are already in the ladder
      └─► S4 writes receipt with raw proof
          └─► S6 Scribe audits it
          └─► S3 Referee passively scores it (Phase B)
      └─► S7 Surgeon keeps the process alive throughout
      └─► S8 Cartographer keeps the roster fresh for next time
```

Matt's role:
1. **Type intent** (into Hermes, which consults the brain)
2. **Approve high-stakes work** (approval gate, automatic notification)
3. **Watch the dashboard** (passive — see quota, quality, failovers)
4. **Fix repair items** (when a specialist escalates — e.g., re-auth a provider, restart LM Studio)
5. **Adjust policy** (per-folder `.orchestrator-policy.yaml` to loosen/tighten)

---

## Part 5: Verification (how we know it works)

### End-to-end test

```bash
# 1. Fix the venv
pip install -e .

# 2. Start the orchestrator
.\scripts\start-orchestrator.ps1 -WatchdogOnce

# 3. Verify all specialists are active
curl -s http://127.0.0.1:8765/api/dashboard-status | python -m json.tool
# Check: route_api_ready=true, services>0, data_sources.subscriptions != null

# 4. Run a real dispatch
curl -s -X POST http://127.0.0.1:8765/api/dispatch \
  -H "Content-Type: application/json" \
  -d '{"text":"classify this text as positive or negative: I love this"}' | python -m json.tool
# Check: state=completed, receipt with proof_kind=live

# 5. Verify passive quality scoring (Phase B)
python -c "
import sqlite3
con = sqlite3.connect('.runtime/orchestrator/state.sqlite')
con.row_factory = sqlite3.Row
rows = con.execute('SELECT COUNT(*) as c FROM operation_quality_scores WHERE proof_kind=\"live\"').fetchone()
print(f'live quality scores: {rows[\"c\"]}')
"
# Check: > 0

# 6. Run the acceptance battery
python -m orchestrator.cli.main acceptance-battery 2>&1 | tail -20
# Check: state=passed, F1-F8 all passed

# 7. Run the spec status
python -m orchestrator.cli.main spec-status 2>&1 | tail -10
# Check: 24 passed, 0 partial, 0 missing

# 8. Kill the process and verify restart
# (kill the python process on port 8765)
.\scripts\start-orchestrator.ps1 -WatchdogOnce
# Check: "watchdog_restart pid=XXXX healthy=True"
# This is the CT-23 crash survival proof
```

### Test suite

```bash
python -m pytest -q
# Expected: 202+ passed (existing tests + new tests for passive scoring, tournament task, acceptance task)
```

---

## Part 6: Risks and Open Questions

1. **The venv fix is a prerequisite** — if `pip install -e .` doesn't work in the Hermes venv (Windows editable install issues), fall back to `pip install aiosqlite PyYAML watchdog ...` with explicit package names.

2. **The model-backed classifier (Phase D)** uses the flat-rate Ollama floor. If Ollama is not running, the classifier falls back to regex (already the current behavior). This is safe.

3. **The passive scoring seam (Phase B)** is best-effort. If `validate_task` throws, the dispatch still completes. The score is not persisted, but the work is not blocked.

4. **The acceptance battery as a scheduled task (Phase E)** runs the full F1-F8 proof daily. This includes the Ralph execution gate, which manipulates Claude state files. If Claude is not installed/configured, F1 will fail. This is correct — it surfaces a real gap.

5. **The watchdog is external to the process** — if the terminal that started it closes, it stops. The mitigation is `install-startup-task.ps1` (Windows Task Scheduler). Verify this is installed.

6. **The Hermes provider routing** (`brain_bridge.py:32-42`) only maps Ollama local/cloud to `hermes_provider`. Claude/Codex/Copilot/Gemini are not mapped. This means the brain can recommend them, but the Hermes shim doesn't know how to switch to them. This is the Phase 3 inversion gap — it needs a `hermes config set model` call per provider. This is a larger task and may be deferred.

---

## Implementation Order (by leverage)

1. **Phase A** (venv fix) — prerequisite, unblocks everything
2. **Phase B** (passive quality scoring) — highest leverage, makes quality rankings live
3. **Phase E** (daily acceptance battery) — proves the system works, daily
4. **Phase C** (weekly tournament) — keeps rankings fresh from controlled tests
5. **Phase F** (roster refresh) — keeps the roster current
6. **Phase G** (route explain endpoint) — dashboard visibility
7. **Phase D** (model-backed classifier) — improves classification accuracy
8. **Phase A2** (verify startup chain) — end-to-end proof

Each phase is independently shippable. Each phase has its own test. The system keeps working at every intermediate state.
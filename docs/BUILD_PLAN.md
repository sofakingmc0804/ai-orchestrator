# AI Operating System — End-to-End Build Plan
**From verified current state → absolute embodiment**
Repo: `C:\Users\Couch\dev\ai-orchestrator` · Author of plan: Claude Code · Status: awaiting approval

---

## Context (why this exists)

`ai-orchestrator` accreted **three incompatible conceptions** in ~9 days, each declared "complete" by a lenient self-tracker:
- **Layer A** (v4.0 spec): a standalone, selection-driven dispatch *app*.
- **Layer B** (06-10 consolidation): a 96-worker *governor* — roster, job classes, budget probes, receipts, dashboards.
- **Layer C** (06-12+): inject governance policy into the premium agents' config + skill-hook gating + token measurement.

A skeptical multi-agent audit established the real state (evidence in repo, not paperwork):
- **Dispatch is genuine**, not simulated — `orchestrator/adapters/builtins.py` makes real HTTP/subprocess calls to Ollama, Claude, Codex, Hermes, etc. Local Ollama + Hermes produce real output today.
- **The scoreboard lies by construction**: `orchestrator/spec_status.py` decides "passed" by counting DB rows and `_has_file()` existence (e.g. CT-05 selection "passed" because `app.js` exists; CT-12 `dispatch_proven=12/12` from completed rows, ≥2 hand-imported with `proof_type:"external_proof_import"`). "23/24 passed / 100% complete" is an upper bound on *claims*, not capability.
- **The real quota signal is bypassed**: `orchestrator/discovery/subscription_usage.py` makes authentic OAuth probes (live ChatGPT/Claude/Gemini windows), but routing consumes the placeholder `budget_probes` table (`999999` sentinels) — `dispatcher.py:125`.
- **The primary UI never runs**: `orchestrator/ui/server.py` uses `@app.on_event("startup")`, removed in the installed FastAPI 0.115.14 / Starlette 1.0.0 → `TypeError`; `orchestrator/main.py:12` silently falls back to a read-only stdlib server. The interactive `app.js` is orphaned (no page loads it).
- **The C-layer "gate" doesn't gate** — `orchestrator/skills/gate.py` only returns advisory `additionalContext`; never a deny.
- **No always-on process** — everything is per-CLI-invocation; the scheduler thread dies with the web process.

**Resolution (owner-approved):** these are not three rival products — they are subsystems of one **AI Operating System**. **Hermes Agent (Nous Research, on this PC) becomes the kernel + shell + entry point** (it already has a default→fallback chain, a `model-router` skill, `/model` switching, persistent memory/skills/cron — per `docs/hermes/HERMES_WALKTHROUGH_2026-06-09.md`). **The orchestrator becomes the vendor-neutral "brain" Hermes consults** — capability/quality model, live quota, failover policy, receipts, governance. This **inverts** today's coupling (orchestrator no longer dispatches *at* Hermes via `hermes -z`).

**Owner directives binding this plan:**
1. **Topology:** Hermes shell + orchestrator brain service it consults. Intelligence stays vendor-neutral in the repo (preserves spec P5: capability-driven, no service-name routing in the brain).
2. **Quality = unbiased peer comparison:** "The only true and absolute real scoring possible is their accuracy as compared against the work of each other." Models run the *same* tasks; score by *relative accuracy vs each other*; the evaluator must be **unbiased** (deterministic referees + ground truth where possible; blind, multi-judge consensus where not; never a single model, never self-judging).
3. **Governance = tiered:** the agents must execute a standard of steps, *and* be efficient (they are the most costly, theoretically the best quality) — enforce the standard, measure the efficiency, let the comparative eval police whether the premium price buys premium quality.
4. **First slice:** "See the right model, for real" — make the system tell the truth (real quota + measured quality + honest scoreboard, visualized) before it acts on it.

---

## Finished-State Definition (what "absolute embodiment" means — all testable)

The build is **done** when every line below passes its acceptance test on this machine:

- **F1 — Truthful self-knowledge.** No capability, score, quota number, or "proven" claim exists without persisted, re-runnable **behavioral evidence** (raw proof artifact). `spec-status` reports *missing* on a clean DB; a target flips to *passed* only after a real run writes real proof. No row-count, file-existence, or self-reported verification "passes" remain. Consequential completion proof is part of F1: a deliberately failing Ralph verification command and an old hand-typed `ralph-verify` pass must both block completion; only `ralph-verify-exec` with a zero exit code may allow.
- **F2 — Unbiased comparative capability model.** For every operation-domain, the system holds a **relative ranking of models by measured accuracy against each other**, produced by a deterministic, bias-controlled tournament (ground-truth referees + blind multi-judge consensus). "Which model for X?" returns an evidence-backed answer with the comparison behind it.
- **F3 — Live resource truth.** Routing decisions consume the **real** subscription/quota signal (`subscription_usage_snapshots`), with a 20% reserve enforced per provider; the placeholder lane is gone or demoted to explicit fallback.
- **F4 — Never stops / never runs out.** An **N-deep failover ladder** (ranked by health → live quota → measured quality-for-task → marginal cost) degrades gracefully to a **flat-rate floor** (Ollama Cloud open-weights) before work ever halts. Killing the top provider mid-run continues the work on the next rung without losing the task.
- **F5 — Hermes is the kernel; orchestrator is the brain.** Hermes' router calls the orchestrator brain (`route` API) for every nontrivial decision; the orchestrator no longer shells `hermes -z` as a worker-of-last-resort. The brain runs as a **supervised always-on service** that restarts on crash.
- **F6 — Premium agents governed.** Claude/Codex/Cowork execute the standard of steps (a small hard-enforced ruleset), their **tokens-per-completed-directive** is measured (not just instruction bytes), and the eval surfaces whether their cost is justified. High-stakes ungrounded claims also use the bounded independent critic path: source-specific objection, no self-judge chain-of-thought, at most three revision cycles, then `UNCERTAIN`.
- **F7 — Always-visualized.** A working dashboard shows, in real time: what to use for what (comparative leaderboards), live quota/flowmeters, the failover ladder and recent failover events, and governance/efficiency receipts. The broken FastAPI path is fixed or retired — no silent fallback.
- **F8 — One honest source of record.** `efficiency_policy.json` remains the vendor-neutral source compiled into each agent's config; AGENTS.md/CLAUDE.md carry no un-retired contradictory (pre-ADR-028) content; the "100% complete" docs are retired. Prose-format `EPISTEMIC_BLOCK` gates are retired from live Claude/Codex/Gemini configs; the retained engine is audit-only and must return `allow`.

---

## Architecture — the OS layer map

```
            ┌─────────────────────────────────────────────────────────┐
   YOU ───► │ KERNEL + SHELL = HERMES DESKTOP (entry point you talk to)│
            │  chat · /model · memory · skills · cron · failover chain  │
            └───────────────┬─────────────────────────────────────────┘
                            │ consults (route/quota/quality)   ◄── INVERSION SEAM
            ┌───────────────▼─────────────────────────────────────────┐
            │ BRAIN = ai-orchestrator (vendor-neutral, always-on svc)  │
            │  ┌────────────┐ ┌────────────┐ ┌───────────┐ ┌─────────┐ │
            │  │ Capability │ │ Resource   │ │ Scheduler │ │Governance│ │
            │  │ + Quality  │ │ Manager    │ │ +Failover │ │ (C-layer)│ │
            │  │ (tournament│ │ (live quota│ │ (ladder,  │ │ enforce  │ │
            │  │  rankings) │ │  +reserve) │ │  always-on│ │ +measure)│ │
            │  └─────┬──────┘ └─────┬──────┘ └─────┬─────┘ └────┬────┘ │
            │        └──────── TELEMETRY / RECEIPTS (truth) ─────┘      │
            │                 SHELL/VISUALIZE (dashboard)              │
            └───────────────┬─────────────────────────────────────────┘
                            │ drivers (native protocols)
            ┌───────────────▼─────────────────────────────────────────┐
            │ WORKERS: Ollama(local/cloud) · Claude · Codex · Copilot  │
            │ · Gemini · Hermes · OpenClaw · LM Studio                 │
            └─────────────────────────────────────────────────────────┘
```

**Inversion seam (the one architectural move everything hangs on):** today `orchestrator/adapters/builtins.py:304` dispatches *to* Hermes. Reverse it — the brain exposes `route` as CLI + HTTP; Hermes' `model-router` skill (or its `hermes.ps1` shim, which already calls a governor at `.runtime/ai-resource-governor/bin/hermes.ps1:78`) calls the brain to pick the worker, then Hermes executes. Brain stays vendor-neutral: no Hermes-specific logic enters routing/quality/quota.

---

## Phase 0 — Integrity Foundation *(prerequisite; the system stops lying)*
**Goal:** every signal becomes evidence-backed, so all later work is measured honestly.

**Sprint 0.1 — Behavioral proof, not row counts**
- Make dispatch persist raw proof: in `orchestrator/dispatch/dispatcher.py` `_complete_dispatch`, persist `result['raw']` (subprocess returncode/stdout or HTTP json) onto the receipt; add `raw_output`, `proof_kind` (`live` | `imported` | `synthetic`) to the `receipts` table (`orchestrator/state/schema.sql`, `store.record_dispatch`).
- Rewrite `orchestrator/spec_status.py` so each target's `passed` requires real evidence: a dispatch is "proven" only if its receipt has `proof_kind='live'` with persisted raw proof; selection modes require an actual selection→dispatch round trip, not `_has_file()`; latency requires a real benchmark receipt. Keep the honest CT-12 guard already present (`tests/test_spec_status.py:96` — `dispatch_proven=0/12` when unproven).
- **Outcome requirements:** on a freshly-initialized DB, `orchestrator spec-status` reports mostly `missing`; no target passes without a live-proof artifact. The 4 imported codex/claude "proof" rows are reclassified `imported` and excluded from `dispatch_proven`.
- **Verify:** `python -m orchestrator.cli.main --home <tmp> spec-status` on an empty home → near-zero passes; run one real Ollama dispatch → exactly the proven targets flip.

**Sprint 0.2 — Retire the false record (GATE 6)**
- Replace `README.md`, `CONSOLIDATION_STATUS.md`, `docs/MIGRATION_COMPLETE.md`, `docs/SNAPSHOT_FINAL.md` "100% complete" claims with a single honest status doc keyed to the new behavioral scoreboard; archive predecessors with dated suffixes.
- Reconcile `AGENTS.md`: remove the pre-ADR-028 "MSI is core / Dell supplementary / AI Resource Governor shim" content above the managed block (it contradicts the governance block in the same file — the GATE-6 tooling can't fix it).
- **Outcome:** one canonical status doc; no file asserts completion the scoreboard can't prove; AGENTS.md internally consistent.
- **Verify:** `grep -ri "100% complete\|all 6 phases"` returns only archived files.

---

## Phase 1 — Sprint 1 Deliverable: "The brain tells the truth" *(the first end-to-end value)*
**Goal:** a trustworthy, visualized "use X for Y" recommendation backed by **real quota + first measured quality + honest scoreboard.**

**Sprint 1.1 — Wire the real quota signal into routing (F3)**
- At `orchestrator/dispatch/dispatcher.py:125`, stop reading the placeholder `budget_probes` table; build the budget lookup from `subscription_usage_snapshots` (latest per `service_id`), translating `tokens_remaining/tokens_limit/ok` into the shape `worker_routing._budget_score`/`_reserve_rejection_reason` expect (`worker_routing.py:142-191, 291-298`). Keep `budget_probes` only as an explicit fallback when a real snapshot is stale/failed.
- Extend `orchestrator/provider_aliases.py` so `subscription_usage` `service_id`s map to roster `provider_id`s (`openai_chatgpt→codex`, `anthropic_claude→claude`, etc.).
- **Outcome (F3):** routing refuses/deprioritizes a provider below its 20% reserve using **live** numbers; the only static fuel-gauge is gone.
- **Verify:** new `tests/test_routing_live_quota.py` — seed a low-remaining snapshot, assert the router reserves/skips that provider; seed healthy, assert it routes.

**Sprint 1.2 — Seed comparative quality (minimum viable tournament)**
- Stand up the closed loop end-to-end on the *existing* deterministic operations: new `operation_quality_scores` table (`dispatch_id, worker_id, operation_domain, validator_name, composite_score, dimensional_scores_json, created_at`); after a dispatch, run `benchmarks/validators/operation_validators.validate_task(...)` and persist the score (`store.record_dispatch` seam).
- Run a first **head-to-head battery**: a small fixed operation set (`benchmarks/fixtures/operations/first_pack.json`) executed across the top candidate models on identical inputs; rank by **relative composite** within each domain (the validators already define "relative standing in the population, not absolute" — `operation_validators.py:15`).
- Feed live scores into the roster: `worker_roster_builder.load_live_operation_quality_scores()` + rerank candidates in `route_with_workers` (raise measured-quality weight in the composite at `worker_routing.py:387-397`; demote the inferred-stat and static-benchmark weights).
- **Outcome (F2 seed):** for the seeded domains, "which model for X" is answered by *measured relative accuracy*, not reputation. Every score links to a re-runnable proof.
- **Verify:** `tests/test_quality_loop.py` — dispatch a known-answer op across 2 fake-but-deterministic workers, assert scores persist and the higher-accuracy worker ranks first.

**Sprint 1.3 — Fix the dashboard so you can SEE it (F7, minimal)**
- Fix `orchestrator/ui/server.py`: replace `@app.on_event("startup")` with a Starlette `lifespan` context manager; remove the silent `try/except` in `orchestrator/main.py:12` so a UI failure is loud, not hidden.
- Make `app.js` live: load it from the served pages; bind it to the existing JSON endpoints (both servers already expose `/api/status,/api/workers,/api/budget,/api/token-flow,/api/receipts` — 30+ shared endpoints). Add a `/api/route` endpoint wrapping `cmd_route`/`route_intent_worker_aware` so the dashboard can show a live "recommend a model for this task" panel with the comparison and live quota behind it.
- **Outcome (F7 minimal + Q4):** open one page, type a task, see the recommended model **with its measured-quality ranking and live quota reasoning** — the truthful "see the right model, for real" slice.
- **Verify:** start server (no fallback), load page, submit a task → recommendation renders from real data; `tests/test_ui_api.py` extended to cover the primary FastAPI app (not just `simple_server`).

**Phase 1 finished state:** the system tells the truth about quota and (seeded) quality, and you can see the right model to use, end-to-end.

---

## Phase 2 — The Comparative Evaluation Engine *(the crown jewel; F2 in full)*
**Goal:** an industrial, **unbiased, deterministic, model-vs-model tournament** producing relative per-operation rankings, continuously refreshed from real work.

**Sprint 2.1 — Operation battery + ground truth**
- Expand `benchmarks/manifest.json` operation domains so every job-class in `job_classifier.py` maps to ≥1 operation with **objective ground truth** where possible (math/regex/reconciliation/extraction have verifiable answers → deterministic referee = unbiased by construction).
- **Outcome:** each domain has a fixed, versioned task set with known-correct answers or deterministic validators.

**Sprint 2.2 — Blind multi-judge panel for open-ended tasks**
- For domains without mechanical ground truth (writing, analysis, agentic), build a **bias-controlled judge**: anonymize/shuffle candidate outputs (judges never see which model produced what), score with a **panel of ≥3 diverse-provider models** (never a model judging its own family), take consensus/median, and reject high-variance verdicts for re-judging. Judges run on the flat-rate floor (near-zero cost). Record inter-judge agreement as a confidence signal.
- **Outcome (F2 unbiased):** open-ended quality is scored by blind consensus across vendors, not a single biased judge — "accuracy as compared against each other," with bias controls auditable in the receipt.

**Sprint 2.3 — Tournament runner + closed loop from real work**
- A scheduled tournament (`orchestrator/scheduler`) periodically runs the battery across all live workers on identical inputs, persists per-(worker,operation) relative scores, and updates the roster. Additionally, **passive scoring of real dispatches** (Sprint 1.2 loop) keeps rankings fresh from actual production work, not just synthetic batteries.
- **Outcome:** rankings reflect both controlled tournaments and live effectiveness; stale models decay.
- **Verify:** `tests/test_tournament.py` — identical task to N deterministic workers → correct relative ranking, blind judge anonymization proven, self-judge exclusion proven, consensus math correct.

---

## Phase 3 — Inversion of Control: Hermes consults the brain *(F5, the topology)*
**Goal:** Hermes becomes the kernel that asks the brain; the brain stops being a Hermes-worker.

**Sprint 3.1 — Brain `route` service**
- Harden `orchestrator/cli/governance.cmd_route` (already emits a JSON `RoutingDecision`) and add `POST /api/route` (FastAPI) returning the ranked ladder + reasons from `route_intent_worker_aware`. This is the **single public brain API**.
- **Outcome:** `orchestrator route --job-class X --text "..."` and `POST /api/route` return an identical, evidence-backed ranked decision.

**Sprint 3.2 — Hermes calls it**
- Re-verify current Hermes state first (`hermes status`, `model-router` skill present, Nous/Codex login) — the walkthrough is 4 days old. Then wire Hermes' `model-router` skill (or `.runtime/ai-resource-governor/bin/hermes.ps1:78`, which already calls a governor pre-dispatch) to call the brain `route` API and act on its choice.
- Demote/retire `HermesAgentAdapter`'s role as the orchestrator's dispatch target (`builtins.py:304`): Hermes is now upstream, not a downstream worker (it remains a *worker* the brain can recommend, but the brain no longer drives Hermes).
- **Outcome (F5):** every nontrivial Hermes turn consults the brain; the brain holds zero Hermes-specific routing logic (P5 preserved).
- **Verify:** a Hermes prompt produces a brain `route` receipt; routing code contains no `hermes`-name special-casing (grep).

---

## Phase 4 — Never Stop, Never Run Out *(F4 + always-on)*
**Goal:** work degrades gracefully down a ranked ladder to a flat-rate floor, on a supervised always-on kernel.

**Sprint 4.1 — N-deep failover ladder**
- Generalize routing from "pick one" to **return an ordered ladder**, ranked by: health → live remaining quota (Phase 1) → measured quality-for-this-operation (Phase 2) → marginal cost. The dispatcher/Hermes walks the ladder on error/exhaustion (extend the existing retry/fallback machinery — `tests/test_retry_fallback.py` proves the loop) **without losing the task/conversation** (Hermes already preserves conversation across `/model`).
- Define the **floor**: flat-rate Ollama Cloud open-weights (kimi-k2.6 / deepseek-v4-pro, "zero marginal cost" per walkthrough); local Ollama is an explicit weak last resort (this box realistically serves only ~0.5B-class).
- **Outcome (F4):** exhaust/kill the top rung mid-task → work continues on the next; the floor is reached before any hard stop.
- **Verify:** `tests/test_failover_ladder.py` — force top-rung quota-exhaustion + health-down, assert the task completes on a lower rung; assert the floor is flat-rate.

**Sprint 4.2 — Always-on supervised kernel**
- Stand up a persistent supervised service (brain HTTP app + scheduler tick + health/repair loop). Reuse: `scheduler/cron.start_due_scheduler_thread` (daemon pattern), `process/recovery.repair_core_services` (health source), `scripts/start-orchestrator.ps1` (port-bind idempotency). Build: a health-check daemon, a watchdog, graceful shutdown (`orchestrator/main.py`), and a retry/backoff wrapper; register via `scripts/install-startup-task.ps1` (single-machine per ADR-028).
- **Outcome:** the brain survives crashes and machine restart (spec CT-23 made real, with proof); quota probes run every 15 min (`budget_probes_cron`).
- **Verify:** kill the process → it restarts and resumes; `spec-status` CT-23 passes only with a real restart-recovery proof.

---

## Phase 5 — Govern the Premium Agents *(F6; tiered per owner directive)*
**Goal:** Claude/Codex/Cowork follow the standard of steps, run efficiently, and prove their cost is justified.

**Sprint 5.1 — Standard-of-steps enforcement (the part that must hold)**
- Add `enforcement_mode: "advisory" | "hard_deny"` to `SkillHookPlan` (`orchestrator/skills/models.py`); make `orchestrator/skills/gate.py` emit a real **deny** (`{"systemMessage": ...}` / block behavior) for a **narrow owner-defined ruleset** (e.g. forbidden metered routes, unapproved external sends, FP-009 >2-non-read-call plans, GATE-0/GATE-6 violations). Everything else stays advisory + receipted. Wire through `.codex/hooks.json` so the deny is real, not cosmetic.
- **Outcome:** the small mandatory ruleset is genuinely blocked; the rest is steered. Matches enforcement-map.md (hard guarantees only where they must be).
- **Verify:** `tests/test_skill_hook_gate.py` extended — a forbidden-route prompt is denied; an efficiency-only violation is advised, not blocked.

**Sprint 5.2 — Efficiency + cost/quality policing**
- Measure the real KPI: **tokens per completed directive** (not instruction bytes). Extend dispatch/receipt to capture real input/output tokens per `intent_id` and aggregate; add `/api/token-accounting` (per-directive). Keep `measure-token-overhead.ps1` for the always-loaded footprint.
- Close the loop with Phase 2: surface, per premium agent, **cost vs measured comparative quality** — if Claude/Codex cost the most but don't win the operations they're given, that becomes a routing/governance signal.
- **Outcome (F6):** you can see, per agent, tokens/directive and whether the premium price buys premium quality; the brain can route accordingly.
- **Verify:** `tests/test_token_accounting.py` — multi-attempt directive aggregates correctly; dashboard shows cost-vs-quality per agent.

---

## Phase 6 — The Living Dashboard *(F7 in full)*
**Goal:** the OS is always visualized — what to use, when, and why; what's running out; what failed over.
- Build out `app.js` into the real shell view: **comparative leaderboards per operation-domain** (Phase 2), **live quota flowmeters + reserve traffic-lights** (Phase 1), the **failover ladder + recent failover events** (Phase 4), **governance/efficiency receipts + tokens-per-directive** (Phase 5), and the live "recommend a model for this task" panel (Phase 1.3). All from existing `/api/*` endpoints + the new `/api/route`, `/api/token-accounting`.
- **Outcome (F7):** one screen answers "what should I use for what, right now, and am I about to run out?" — always.
- **Verify:** load dashboard with live data; each panel renders from real endpoints; `tests/test_ui_api.py` covers the new endpoints on the primary server.

---

## Phase 7 — Absolute Embodiment / Hardening
**Goal:** prove the finished-state battery; make the bones honest.
- Cross-OS bones honest (CT-20): macOS/Linux stay stable stubs but `spec-status` stops counting stub-file-existence as "passed."
- Autopilot (CT-19) confirmed gated-off with a real default-disabled assertion, not file-existence.
- Full audit trail + new-service registration proof (add the synthetic adapter end-to-end with real proof, not a contract-count).
- Consequential reasoning enforcement proof: the acceptance battery runs the Ralph execution gate checks, verifies prose gates are retired, verifies Codex's in-path reasoning floor is honest (`xhigh` with no fake universal proxy claim), records the conditional Action 4 reality-check tool status, and proves the bounded critic returns a specific objection then `UNCERTAIN` by the third unresolved cycle.
- **Acceptance battery:** one script runs the F1–F8 checks and emits a signed owner receipt. The OS is "done" when it passes.
- **Verify:** `python -m orchestrator.cli.main spec-status` is all-honest-passed **with live proofs**, and the F1–F8 acceptance battery is green.

---

## Cross-cutting invariants (hold in every phase)
- **Evidence or it didn't happen:** no score/quota/"passed" without a persisted, re-runnable proof artifact (F1).
- **Vendor-neutral brain:** no provider-name special-casing in routing/quality/quota (P5).
- **Unbiased eval:** deterministic referee or blind multi-vendor consensus; never self-judging; relative-not-absolute scoring (owner directive 2).
- **No metered API by default; flat-rate/subscription only;** 20% reserve on scarce quota.
- **GATE 6:** every artifact created retires its predecessor in the same action.
- **Single machine (ADR-028):** no MSI/multi-machine routing; no `G:\` runtime contract.

## Critical files (reused, not rebuilt)
- Routing brain: `orchestrator/routing/worker_routing.py` (`route_intent_worker_aware:450`, `route_with_workers:258`), `orchestrator/routing/engine.py` (`route_intent:148`, `_overlay_benchmark_scores:100`), `orchestrator/governance/job_classifier.py` (`classify_intent:131`).
- Brain API surface: `orchestrator/cli/governance.py` (`cmd_route:194`, already JSON), to be mirrored as `POST /api/route`.
- Quota: `orchestrator/discovery/subscription_usage.py` (real probes), `orchestrator/scheduler/budget_probes_cron.py` (15-min hook), swap at `orchestrator/dispatch/dispatcher.py:125`.
- Quality: `benchmarks/validators/operation_validators.py` (`validate_task`, composite scoring), `benchmarks/manifest.json`, `orchestrator/governance/worker_roster_builder.py` (roster + new `load_live_operation_quality_scores`).
- Telemetry: `orchestrator/state/store.py` (`record_dispatch`, `record_token_usage`, `backfill_token_usage_from_receipts:1039`), `orchestrator/dispatch/receipt_enhanced.py:22`.
- Shell: `orchestrator/ui/server.py` (lifespan fix), `orchestrator/ui/static/app.js` (de-orphan), `orchestrator/main.py` (remove silent fallback).
- Always-on: `orchestrator/scheduler/cron.py:29`, `orchestrator/process/recovery.py:163`, `scripts/start-orchestrator.ps1`, `scripts/install-startup-task.ps1`.
- Governance: `orchestrator/skills/{gate,detector,runtime,models}.py`, `.codex/hooks.json`, `scripts/generate-surface-stubs.ps1`, `orchestrator/config/efficiency_policy.json`.

## Sequencing summary
**0** Integrity → **1** Truthful brain + first-slice dashboard *(Q4 deliverable)* → **2** Full comparative eval engine → **3** Hermes-consults-brain inversion → **4** Failover ladder + always-on → **5** Premium-agent governance → **6** Living dashboard → **7** Embodiment battery. Phase 0 gates everything (can't measure progress until the scoreboard is honest); Phases 2 and 4 are the heaviest; 1.x is shippable on its own.

## Top risks
- **Eval bias** is the deepest risk — mitigated by ground-truth-first, blind anonymization, multi-vendor panels, self-judge exclusion, consensus + variance gating (Phase 2). If bias can't be controlled for a domain, fall back to deterministic-only scoring there and mark the domain "comparative-pending."
- **Hermes seam reality** — must re-verify current Hermes state before Phase 3; if the `model-router` skill can't call the brain cleanly, fall back to the `hermes.ps1` shim path (already calls a governor pre-dispatch).
- **Always-on on a single box** — the floor's flat-rate cloud sub has its own 5h/weekly caps; the ladder must *know* them (Phase 1 real quota) to ride them.

## Verification (end-to-end, per phase)
Each phase ships its own `tests/test_*.py` (named above) plus a `spec-status` delta that only advances on **live proof**. Final gate = the F1–F8 acceptance battery green + an honest `spec-status` + a live dashboard demonstrating "see the right model, for real," "never run out" (kill-the-top-rung demo), and "premium-agent cost-vs-quality."

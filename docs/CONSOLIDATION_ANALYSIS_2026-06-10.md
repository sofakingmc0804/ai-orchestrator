# AI Orchestrator Consolidation Analysis
**Date:** 2026-06-10  
**Author:** Hermes Agent (via session analysis)  
**Status:** Draft for Matt Review

---

## Executive Summary

You have **two mature systems** building the same vision, plus **three early-stage fragments** in the AI Projects Folder. The real work is in:

1. **`dev/ai-orchestrator`** — Full orchestrator application (v4.0 spec, 24 capability targets)
2. **`.ai-resource-governor`** — Workforce management layer (worker cards, job classes, budget tracking)

These should be **merged into a single system**, not kept parallel. The governor's worker modeling is more sophisticated; the orchestrator's adapter architecture and dispatch workflow are more complete.

---

## System Inventory

### 1. `C:\Users\Couch\dev\ai-orchestrator` ✅ **PRIMARY CANDIDATE**

**What it is:** Standalone user-facing AI operating layer  
**Maturity:** 70-80% complete (17/24 capability targets passed or partial)  
**Codebase:** Python, FastAPI, SQLite, ~15K LOC

**Strengths:**
- Complete v4.0 specification (989 lines, 10 architectural principles)
- 11 adapter contracts (Claude Desktop/CLI, Codex, Ollama HTTP/CLI, Copilot, Gemini, Hermes, OpenClaw, LM Studio)
- Working dispatch engine with approval gates, retry/fallback, repair queue
- Notification spine (tray, email, in-app) with subscriber pattern
- Project discovery and per-project policy enforcement
- Benchmark infrastructure with operation scores and latency tracking
- State store schema (services, capabilities, intents, dispatches, quota_ledger, working_memory, audit_log)
- UI/API surface (FastAPI + static frontend)
- Spec status tracker (`spec_status.py`) with evidence-based progress measurement

**Weaknesses:**
- Routing engine uses simple billing_class + quality scoring
- No worker specialization modeling (doesn't know which model is good at which job type)
- Quota tracking exists but budget probes are shallow
- Doesn't model the same model on different surfaces as different workers

**Key Files:**
```
orchestrator/
  adapters/          # 11 adapter implementations
  dispatch/          # Dispatcher with approval gates, retry logic
  routing/           # Capability-driven routing engine
  state/store.py     # SQLite state layer (927 lines)
  notifications/     # Spine + subscribers (tray, email, in-app)
  intent/            # Intent interpreter
  platform/          # Cross-OS abstractions (Windows, macOS, Linux)
docs/specs/
  AI_ORCHESTRATOR_SPEC_v4.0.md  # The canonical spec
```

---

### 2. `C:\Users\Couch\.ai-resource-governor` ✅ **CRITICAL COMPONENT**

**What it is:** AI resource governance and workforce modeling  
**Maturity:** 60-70% complete (worker roster v2 terminal, budget probes partial)  
**Codebase:** Python, SQLite, ~2K LOC + 234KB JSON roster

**Strengths:**
- **Worker Card Schema** — Models each model+surface combination as a distinct worker with:
  - Specialization stats (coding, debugging, architecture, speed, long_context, etc.)
  - Contract type (local_resource, subscription_quota, metered_extra_cost, etc.)
  - Budget source and overtime rules
  - Best jobs / avoid jobs lists
  - Hardware fit (cloud_only, local_ok, local_preferred)
- **Job Class System** — 14 job classes with required capabilities, preferred stats, approval floors:
  - `routing_triage`, `bulk_extraction`, `schema_validation`
  - `simple_coding`, `repo_coding`, `agentic_repair`, `deep_debugging`
  - `architecture`, `security_review`, `long_context_synthesis`
  - `visual_reasoning`, `ocr_document_intake`, `public_content`, `business_sensitive`
- **Budget Probes** — Live quota readback for Copilot (working), Claude/Codex/Ollama Cloud (partial)
- **Receipt System** — Every model call logged with task classification, worker chosen, cost, outcome
- **Hermes Integration** — Compact governed catalog, split-brain config resolution logic

**Weaknesses:**
- No adapter layer — just routes to Hermes CLI or OpenClaw gateway
- No dispatch workflow, approval gates, or notification spine
- No UI — CLI-only (`ai-route job --task <class> --surface <surface>`)
- Worker roster is static JSON (7888 lines), not dynamically discovered
- Doesn't integrate with orchestrator's capability contracts

**Key Files:**
```
.ai-resource-governor/
  ai_governor.py          # Main routing logic (1096 lines)
  worker_roster_v2.json   # 7888 lines of worker cards + job classes
  scripts/
    build-worker-roster-v2.py  # Roster builder (1379 lines)
    select-worker-v2.py        # Worker selection for job class (235 lines)
  inventory.sqlite        # Budget state, receipts, repair queue
  policy.json             # Routing policy (fallback order, reserve %, app defaults)
```

---

### 3. `C:\Users\Couch\AI Projects Folder\Forge For VS Code` ❌ **NOT AN ORCHESTRATOR**

**What it is:** VS Code extension with simple "vibe router"  
**Maturity:** 10-20% — router is a stub (14 lines of TypeScript)  
**Verdict:** Abandon orchestrator ambitions, keep as VS Code integration point only

**Analysis:**
- `VibeRouter` defaults to "Universal Agent for everything"
- No capability registry, no routing logic, no state management
- Has good AGENTS.md protocol (Stacked Interpreter System) but that's behavioral, not architectural
- **Recommendation:** Strip out orchestrator code, make it a thin client that calls the real orchestrator

---

### 4. `C:\Users\Couch\AI Projects Folder\enhanced-notebooklm` ❌ **UNIMPLEMENTED SPEC**

**What it is:** Agentic RAG orchestration spec (not code)  
**Maturity:** 0% — spec says "0/12 files created, 0% complete"  
**Verdict:** Archive the spec, don't implement — this is a subset of what the main orchestrator does

**Analysis:**
- 289-line spec for multi-agent RAG (Decomposer, Retriever, Critic, Synthesizer)
- Would be a **capability** within the main orchestrator, not a separate system
- **Recommendation:** If agentic RAG is needed, implement it as an orchestrator adapter or skill

---

### 5. `C:\Users\Couch\AI Projects Folder\Brewery ERP Software\tools\auto-dev` ❌ **SINGLE-PROJECT SCOPE**

**What it is:** Autonomous development loop for one project  
**Maturity:** 40-50% — working Orchestrator class (556 lines TypeScript)  
**Verdict:** Keep as project-local tool, don't merge — too narrow in scope

**Analysis:**
- Orchestrator loops: Architect → Coder → Verifier → retry on errors
- Uses local Ollama (`qwen2.5-coder:7b`)
- Project-specific: hardcoded workspace root, verification commands
- **Recommendation:** This is a **use case** for the main orchestrator (job_class = `agentic_repair`), not a competing system

---

## Overlap Analysis

### What Both Systems Do (Duplicate Effort)

| Function | Orchestrator | Governor | Should Be |
|----------|--------------|----------|-----------|
| Model registry | capability contracts | worker cards | **Merge: worker cards with capability contracts** |
| Routing | billing_class + quality | job_class + worker stats | **Merge: governor's job_class system** |
| Quota tracking | quota_ledger table | budget probes + receipts | **Merge: governor's probes, orchestrator's ledger** |
| State storage | SQLite (state/store.py) | SQLite (inventory.sqlite) | **Merge: single DB, orchestrator's schema extended** |
| Hermes integration | hermes-agent adapter | governed catalog + config resolution | **Merge: governor's config logic into adapter** |

### What Only Orchestrator Does (Keep)

- Adapter layer (11 services with native protocols)
- Dispatch workflow (approval gates, retry, fallback, repair queue)
- Notification spine (tray, email, in-app subscribers)
- Project discovery and per-project policy
- UI/API surface (FastAPI + frontend)
- Spec status tracking with evidence

### What Only Governor Does (Integrate)

- Worker specialization modeling (stats per job type)
- Job classification system (14 job classes)
- Budget probes (live quota readback per provider)
- Receipt system (task → worker → outcome logging)
- Overtime rules and approval floors per job class

---

## Consolidation Strategy

### Phase 0: Decision (You Decide)

**Question:** Do you want one unified system, or keep them separate with governor as a "routing library" for orchestrator?

**Recommended:** Full merge — governor becomes `orchestrator/governance/` module, single DB, single CLI, single UI.

---

### Phase 1: Architectural Merge (Week 1-2)

**Goal:** Governor logic inside orchestrator repo, no duplicate state.

**Steps:**
1. Copy `.ai-resource-governor/worker_roster_v2.json` → `dev/ai-orchestrator/data/rosters/worker_roster_v2.json`
2. Copy `build-worker-roster-v2.py` + `select-worker-v2.py` → `orchestrator/governance/`
3. Extend orchestrator's `state/store.py` with governor tables:
   - `worker_cards` (from governor)
   - `job_classes` (from governor)
   - `budget_probes` (new — stores last probe result per provider)
   - `model_receipts` (from governor's receipts/)
4. Merge `policy.json` into orchestrator's config system
5. Update orchestrator's `routing/engine.py` to use governor's job_class + worker stats scoring

**Deliverable:** `orchestrator cli dispatch --job-class <class> --text "<intent>"` uses worker roster for routing.

---

### Phase 2: Worker Card Integration (Week 2-3)

**Goal:** Every adapter registers worker cards, not just capability contracts.

**Steps:**
1. Extend `contract.yaml` format to include worker stats:
   ```yaml
   adapter: claude-code-cli
   workers:
     - worker_id: claude-sonnet-4.6@claude-code-cli
       base_model: Claude Sonnet 4.6
       surface: claude-code-cli
       stats:
         coding: 9
         agentic_loop: 8
         debugging: 8
         architecture: 7
       best_jobs: ["repo_coding", "agentic_repair", "deep_debugging"]
       avoid_jobs: ["bulk_extraction", "ocr_document_intake"]
   ```
2. Update `build-worker-roster-v2.py` to generate cards from adapter contracts + live probes
3. Add `orchestrator cli refresh-workers` command

**Deliverable:** Worker cards are data, not hardcoded JSON.

---

### Phase 3: Budget Probe Unification (Week 3-4)

**Goal:** Live quota state for all subscription providers, visible in UI.

**Steps:**
1. Move governor's budget probes to `orchestrator/scheduler/` (run every 15 min)
2. Store results in `budget_probes` table with schema:
   ```sql
   CREATE TABLE budget_probes (
       provider_id TEXT,
       probe_type TEXT,  -- 'copilot_premium', 'claude_console', 'codex_account', 'ollama_cloud_usage'
       remaining INTEGER,
       limit INTEGER,
       reset_at TEXT,
       probed_at TEXT,
       ok BOOLEAN,
       error TEXT
   );
   ```
3. Add UI panel: "Provider Quota Status" with traffic lights (green > 50%, yellow 20-50%, red < 20%)
4. Router enforces reserve thresholds per consequence tier

**Deliverable:** UI shows real-time quota state, routing refuses dispatch when below reserve.

---

### Phase 4: Receipt System Merge (Week 4-5)

**Goal:** Every dispatch creates a receipt, searchable for future routing improvements.

**Steps:**
1. Extend orchestrator's `receipts` table with governor's receipt fields:
   - `job_class` (what type of work was this?)
   - `worker_id` (which model+surface was used?)
   - `outcome_quality` (1-5, from user feedback or automated validation)
   - `escalated_from` (if this was a fallback from a cheaper worker)
2. Add `orchestrator cli receipt --intent <id>` to view receipt
3. Add `orchestrator cli receipts --job-class <class> --last 50` for analysis
4. Use receipt history to adjust worker stats over time (learning system)

**Deliverable:** Every dispatch is logged with job classification and worker choice.

---

### Phase 5: UI/CLI Unification (Week 5-6)

**Goal:** One CLI, one UI, no more `ai-route` vs `orchestrator cli` confusion.

**Steps:**
1. Deprecate `.ai-resource-governor/bin/ai-route.ps1` → wrapper that calls `orchestrator cli route`
2. Add CLI commands:
   - `orchestrator cli route --job-class <class> --text "<intent>"` — dry-run routing decision
   - `orchestrator cli workers --job-class <class>` — list workers suited for job type
   - `orchestrator cli budget` — show all provider quota state
   - `orchestrator cli receipts --last 50` — recent dispatch receipts
3. Add UI pages:
   - `/workers` — worker roster with stats, filters by job class
   - `/budget` — provider quota status
   - `/receipts` — searchable dispatch history

**Deliverable:** Single CLI (`orchestrator cli`), single UI (`http://localhost:8765`).

---

### Phase 6: Cleanup (Week 6-7)

**Goal:** Archive or repurpose fragmented projects.

**Steps:**
1. **Forge For VS Code:** Remove `VibeRouter`, add "Send to Orchestrator" command
2. **enhanced-notebooklm:** Archive `docs/specs/system-agentic-rag-orchestration.md` → `docs/archive/`
3. **Brewery ERP auto-dev:** Keep as-is (project-local tool), document it as a `job_class = "agentic_repair"` example
4. **`.ai-resource-governor`:** Keep as runtime directory only (receipts, cache, DB), move all source code to orchestrator repo

**Deliverable:** No more "which system do I use?" confusion.

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Governor's worker roster becomes stale | Medium | High | Auto-refresh on adapter registration, scheduled probe every 15 min |
| Merge breaks existing dispatch flow | Medium | High | Keep orchestrator's dispatch engine unchanged, only extend routing |
| Budget probes fail silently | High | Medium | Repair queue entries when probes fail, UI shows "last probed" timestamp |
| CLI confusion during transition | High | Low | Deprecation warnings, wrappers that call new commands |
| DB migration loses receipt history | Low | High | Backup before migration, dual-write during transition week |

---

## Recommended Next Steps

**Immediate (this session):**
1. You decide: full merge vs. governor-as-library
2. If merge: create `orchestrator/governance/` directory, copy governor scripts
3. Run `build-worker-roster-v2.py` to generate fresh roster inside orchestrator repo

**This Week:**
1. Extend orchestrator's routing engine to use job_class + worker stats
2. Test: `dispatch --job-class repo_coding --text "fix the auth bug in src/auth.py"`
3. Verify routing picks different workers for `repo_coding` vs `bulk_extraction`

**Next Week:**
1. Budget probe integration
2. UI panel for quota status
3. Receipt system merge

---

## Questions for Matt

1. **Merge strategy:** Full consolidation (recommended) or keep governor as separate "routing library"?
2. **Priority:** What's more valuable — better routing (worker stats) or better visibility (budget UI)?
3. **Timeline:** Do you want this done in 1 week (aggressive) or 3-4 weeks (steady)?
4. **Governor runtime:** Should `.ai-resource-governor/` stay as the runtime directory (receipts, DB, cache) even after source code moves?

---

## Appendix: File Movement Map

### Move to `dev/ai-orchestrator/`

```
.ai-resource-governor/scripts/build-worker-roster-v2.py  →  orchestrator/governance/worker_roster_builder.py
.ai-resource-governor/scripts/select-worker-v2.py        →  orchestrator/governance/worker_selector.py
.ai-resource-governor/worker_roster_v2.json              →  data/rosters/worker_roster_v2.json
.ai-resource-governor/policy.json                        →  orchestrator/config/governance_policy.json
.ai-resource-governor/tests/test_governor.py             →  tests/test_governance/
```

### Keep in `.ai-resource-governor/` (runtime only)

```
receipts/           # Live receipt logs
inventory.sqlite    # State DB (until migration, then deprecated)
cache/              # Hermes catalog, provider caches
bin/                # CLI shims (wrappers calling orchestrator cli)
```

### Archive

```
AI Projects Folder/enhanced-notebooklm/docs/specs/system-agentic-rag-orchestration.md
Forge For VS Code/src/core/universal/orchestrator.ts  (remove VibeRouter)
```

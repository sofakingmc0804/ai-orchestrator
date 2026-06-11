# AI Orchestrator Consolidation Roadmap
**Created:** 2026-06-10  
**Status:** Pending Matt Approval  
**Estimated Effort:** 4-6 weeks (steady pace) or 2 weeks (aggressive)

---

## Decision Required

**Choose one:**

### Option A: Full Merge (Recommended)
- Governor becomes `orchestrator/governance/` module
- Single codebase, single DB, single CLI, single UI
- `.ai-resource-governor/` becomes runtime-only (receipts, cache, DB)
- **Pros:** No more confusion, unified development, cleaner architecture
- **Cons:** 1-2 days of migration work, risk of breaking something during merge

### Option B: Governor as Library
- Governor stays separate, orchestrator imports it as routing library
- Two DBs, two CLIs, orchestrator UI shows governor data
- **Pros:** Less migration risk, governor can be used independently
- **Cons:** Ongoing confusion, duplicate state, harder to maintain

**Recommendation:** Option A — you've already got two systems doing 60-80% of the same job. Merge them.

---

## Phase Breakdown

### Phase 1: Foundation (Days 1-3)
**Goal:** Governor code inside orchestrator repo, both systems still runnable.

#### Task 1.1: Create Governance Module
```bash
mkdir orchestrator/governance
cp .ai-resource-governor/scripts/build-worker-roster-v2.py orchestrator/governance/worker_roster_builder.py
cp .ai-resource-governor/scripts/select-worker-v2.py orchestrator/governance/worker_selector.py
cp .ai-resource-governor/worker_roster_v2.json data/rosters/
cp .ai-resource-governor/policy.json orchestrator/config/governance_policy.json
```

#### Task 1.2: Extend State Store
Add to `orchestrator/state/schema.sql`:
```sql
-- Worker cards (from governor)
CREATE TABLE worker_cards (
    worker_id TEXT PRIMARY KEY,
    model_id TEXT,
    base_model TEXT,
    surface TEXT,
    provider_id TEXT,
    contract_type TEXT,
    salary_bucket TEXT,
    overtime_rule TEXT,
    budget_source_id TEXT,
    hardware_fit TEXT,
    context_window INTEGER,
    capabilities_json TEXT,
    modalities_json TEXT,
    tools_json TEXT,
    stats_json TEXT,
    best_jobs_json TEXT,
    avoid_jobs_json TEXT,
    approval_required BOOLEAN,
    source_evidence_json TEXT,
    last_verified TEXT
);

-- Job classes (from governor)
CREATE TABLE job_classes (
    job_class TEXT PRIMARY KEY,
    required_capabilities_json TEXT,
    preferred_stats_json TEXT,
    local_first BOOLEAN,
    approval_floor TEXT
);

-- Budget probes (new)
CREATE TABLE budget_probes (
    id TEXT PRIMARY KEY,
    provider_id TEXT,
    probe_type TEXT,
    remaining INTEGER,
    limit INTEGER,
    reset_at TEXT,
    probed_at TEXT,
    ok BOOLEAN,
    error TEXT
);
```

#### Task 1.3: Migration Script
Create `scripts/migrate-governor-to-orchestrator.py`:
- Read governor's `inventory.sqlite`
- Insert worker_cards, job_classes into orchestrator DB
- Copy receipts from `.ai-resource-governor/receipts/` to `orchestrator/receipts/`
- Backup both DBs before migration

**Acceptance Criteria:**
- [ ] `python -m orchestrator.cli.main refresh-workers` runs without errors
- [ ] Worker cards visible in SQLite: `SELECT COUNT(*) FROM worker_cards;` returns > 20
- [ ] Governor's original DB untouched (backup exists)

---

### Phase 2: Routing Integration (Days 4-7)
**Goal:** Orchestrator dispatch uses governor's job_class + worker stats for routing.

#### Task 2.1: Intent → Job Class Mapping
Extend `orchestrator/intent/interpreter.py`:
```python
JOB_CLASS_KEYWORDS = {
    "routing_triage": ["classify", "route", "triage", "sort"],
    "bulk_extraction": ["extract", "parse", "scrape", "pull data"],
    "repo_coding": ["fix", "implement", "refactor", "add test"],
    "agentic_repair": ["debug", "investigate", "repair", "tool loop"],
    "architecture": ["design", "architect", "plan", "system"],
    # ... etc
}

def classify_job_class(intent_text: str) -> str:
    # Keyword matching + small local model for ambiguous cases
    # Default to "routing_triage" if unclear
```

#### Task 2.2: Update Routing Engine
Modify `orchestrator/routing/engine.py`:
- Replace current scoring with governor's worker selection logic
- Load job_class requirements, match against worker stats
- Respect approval_floor per job_class
- Keep existing billing_class + quota filters as secondary

#### Task 2.3: CLI Command
Add `orchestrator/cli/main.py`:
```python
@click.command()
@click.option("--job-class", required=True)
@click.option("--text", required=True)
@click.option("--dry-run", is_flag=True)
def route(job_class, text, dry_run):
    """Route a job to the best worker."""
    # Load job class requirements
    # Find matching workers
    # Score by stats match + billing class + quota
    # Return chosen worker + reasoning
```

**Acceptance Criteria:**
- [ ] `orchestrator cli route --job-class repo_coding --text "fix the auth bug"` returns `claude-code-cli` or `copilot-gh`
- [ ] `orchestrator cli route --job-class bulk_extraction --text "extract all invoices"` returns `qwen3.5:4b` or other local model
- [ ] Routing decision includes: chosen worker, why chosen, why alternatives lost, quota state

---

### Phase 3: Budget Probes (Days 8-12)
**Goal:** Live quota state for all providers, visible in UI.

#### Task 3.1: Probe Scripts
Move governor's probes to `orchestrator/scheduler/budget_probes.py`:
- `probe_copilot()` — `gh api /copilot_internal/user`
- `probe_claude()` — Claude Console API or OAuth status
- `probe_codex()` — `codex account status`
- `probe_ollama_cloud()` — Ollama Cloud usage API
- `probe_nous()` — `hermes portal status` (once logged in)

#### Task 3.2: Scheduler Integration
Add to `orchestrator/scheduler/cron.py`:
```python
# Every 15 minutes
@cron.schedule("*/15 * * * *")
async def refresh_budget_probes():
    for provider in PROVIDERS:
        result = await probe_provider(provider)
        await store.upsert_budget_probe(result)
```

#### Task 3.3: UI Panel
Add FastAPI endpoint + frontend page:
```python
@app.get("/api/budget")
async def get_budget_state():
    probes = await store.list_budget_probes()
    return {"providers": probes, "updated_at": ...}
```

Frontend (`orchestrator/ui/static/budget.html`):
- Table: Provider | Remaining | Limit | % Left | Reset At | Status
- Traffic light colors: green (>50%), yellow (20-50%), red (<20%)
- Last probed timestamp

**Acceptance Criteria:**
- [ ] Copilot quota shows ~1,284 of 1,500 remaining (from current state)
- [ ] UI updates every 15 minutes automatically
- [ ] Router refuses dispatch when provider below reserve threshold

---

### Phase 4: Receipt System (Days 13-17)
**Goal:** Every dispatch logged with job_class, worker_id, outcome.

#### Task 4.1: Extend Receipt Schema
Modify `orchestrator/state/schema.sql`:
```sql
ALTER TABLE dispatch_receipts ADD COLUMN job_class TEXT;
ALTER TABLE dispatch_receipts ADD COLUMN worker_id TEXT;
ALTER TABLE dispatch_receipts ADD COLUMN outcome_quality INTEGER;  -- 1-5
ALTER TABLE dispatch_receipts ADD COLUMN escalated_from TEXT;     -- previous worker_id
```

#### Task 4.2: Dispatch Integration
Modify `orchestrator/dispatch/dispatcher.py`:
- After dispatch completes, classify job_class (if not already classified)
- Record worker_id (model+surface)
- Capture outcome (success/failure, tokens, latency)
- Optional: ask user for quality rating (1-5) via notification

#### Task 4.3: CLI Commands
Add:
```bash
orchestrator cli receipts --last 50
orchestrator cli receipts --job-class repo_coding --last 20
orchestrator cli receipt --intent <intent_id>
```

#### Task 4.4: Learning Loop (Future)
- Analyze receipt history weekly
- Adjust worker stats based on outcomes
- Promote workers that succeed, demote workers that fail

**Acceptance Criteria:**
- [ ] Every dispatch creates a receipt with job_class + worker_id
- [ ] `orchestrator cli receipts --last 10` shows recent dispatches
- [ ] Receipt includes: intent, job_class, worker, outcome, latency, tokens

---

### Phase 5: UI/CLI Unification (Days 18-24)
**Goal:** One interface, no confusion.

#### Task 5.1: Deprecate Governor CLI
Create `.ai-resource-governor/bin/ai-route.ps1` wrapper:
```powershell
# DEPRECATED: Use 'orchestrator cli route' instead
& python -m orchestrator.cli.main route @args
```

#### Task 5.2: New CLI Commands
```bash
orchestrator cli workers [--job-class <class>] [--surface <surface>]
orchestrator cli workers show <worker_id>
orchestrator cli budget
orchestrator cli receipts [--job-class <class>] [--last N]
orchestrator cli receipt <intent_id>
orchestrator cli route --job-class <class> --text "<intent>" [--dry-run]
```

#### Task 5.3: UI Pages
Add to `orchestrator/ui/static/`:
- `/workers` — Worker roster table, filterable by job_class, sortable by stats
- `/workers/:id` — Worker detail card (stats, best jobs, recent receipts)
- `/budget` — Provider quota dashboard
- `/receipts` — Searchable dispatch history

**Acceptance Criteria:**
- [ ] All governor CLI commands have orchestrator equivalents
- [ ] UI shows workers, budget, receipts without page refresh (React or htmx)
- [ ] Old governor CLI prints deprecation warning

---

### Phase 6: Cleanup (Days 25-30)
**Goal:** Archive fragments, document the system.

#### Task 6.1: Forge For VS Code
- Remove `src/core/universal/orchestrator.ts` (VibeRouter stub)
- Add command: "Send to Orchestrator" → POST to `http://localhost:8765/api/selections`
- Update AGENTS.md: "For AI orchestration, use the Orchestrator app"

#### Task 6.2: Archive enhanced-notebooklm Spec
```bash
mv "AI Projects Folder/enhanced-notebooklm/docs/specs/system-agentic-rag-orchestration.md" \
   "dev/ai-orchestrator/docs/archive/agentic-rag-spec-2026-02-11.md"
```

#### Task 6.3: Document Brewery ERP auto-dev
Add to `dev/ai-orchestrator/docs/examples/brewery-auto-dev.md`:
- Explain it's a project-local tool, not competing orchestrator
- Show how to replicate with orchestrator: `job_class = "agentic_repair"`

#### Task 6.4: Update Documentation
- `README.md` — Add consolidation notes
- `AI_ORCHESTRATOR_SPEC_v4.0.md` — Add governance layer to architecture diagram
- Create `GOVERNANCE.md` — Explain worker cards, job classes, budget probes

**Acceptance Criteria:**
- [ ] No more "which system do I use?" questions
- [ ] All fragmented projects archived or repurposed
- [ ] Single source of truth: `dev/ai-orchestrator/`

---

## Timeline Options

### Aggressive (2 weeks)
**Days 1-7:** Phase 1 + Phase 2 (foundation + routing)  
**Days 8-14:** Phase 3 + Phase 4 (budget + receipts)  
**Days 15-16:** Phase 5 (CLI/UI, minimal)  
**Skip:** Phase 6 (cleanup can wait)

**Risk:** Higher chance of bugs, less testing time

### Steady (4-6 weeks)
**Week 1:** Phase 1 (foundation)  
**Week 2:** Phase 2 (routing)  
**Week 3:** Phase 3 (budget probes)  
**Week 4:** Phase 4 (receipts)  
**Week 5:** Phase 5 (UI/CLI)  
**Week 6:** Phase 6 (cleanup) + buffer

**Risk:** Lower, time for testing and iteration

---

## Success Metrics

**Functional:**
- [ ] `orchestrator cli route --job-class <class> --text "<intent>"` returns correct worker
- [ ] UI shows live quota state for Copilot, Claude, Codex
- [ ] Every dispatch creates a receipt with job_class + worker_id
- [ ] Routing respects approval_floor per job_class

**Architectural:**
- [ ] No more duplicate state (single DB)
- [ ] No more CLI confusion (single CLI)
- [ ] Governor source code moved, runtime-only remaining
- [ ] Fragmented projects archived or repurposed

**Experiential:**
- [ ] You can ask "Which model should I use for this job?" and get a specific answer with reasoning
- [ ] You never wonder "which system do I use?" anymore
- [ ] Quota burn is visible and predictable

---

## Immediate Next Step

**Run this command to start Phase 1:**

```bash
cd C:\Users\Couch\dev\ai-orchestrator
mkdir -p orchestrator/governance data/rosters
```

Then I'll copy the governor files and create the migration script.

**Do you approve this roadmap? If yes, which timeline (aggressive 2 weeks or steady 4-6 weeks)?**

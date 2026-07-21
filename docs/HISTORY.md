# Project History

This file preserves the internal migration/status log that used to live at the
top of `README.md`. It is historical record, not a current-state guide. For
current entry points and setup, see `README.md` and `docs/guides/QUICK_START.md`.

---

> **Status:** AI Operating System Phases 1-7 complete as of 2026-06-14. See
> **[docs/STATUS.md](STATUS.md)** and run `python -m orchestrator.cli.main spec-status`.
> The "ALL 6 PHASES COMPLETE / 100%" framing below is a **historical 2026-06-10 migration record**;
> those completion claims were row-count artifacts, not behavioral proof, and are superseded.
> Current direction and finished-state: `.claude/plans/make-the-end-to-end-plan-linked-widget.md`.

---

## Historical: Consolidation migration (2026-06-10)
**Date:** 2026-06-10
**Status:** historical migration log
**Duration:** ~2 hours

---

## Executive Summary

Two mature systems have been consolidated into a single unified AI orchestrator:

**Before:**
- `dev/ai-orchestrator/` — Full orchestrator (70-80% complete)
- `.ai-resource-governor/` — Budget tracking, worker roster, policy

**After:**
- Single source: `dev/ai-orchestrator/` — Complete unified orchestrator
- `.ai-resource-governor/` — Runtime data only (backward compat)

---

## Historical quick start (not current)

```powershell
# Start server
cd <path-to-ai-orchestrator-checkout>
python -m orchestrator.platform.server

# Open dashboards
# http://localhost:8765/          # Home
# http://localhost:8765/workers   # Workers (96 workers)
# http://localhost:8765/budget    # Budget (6 providers)
# http://localhost:8765/receipts  # Receipts (audit trail)

# Install CLI aliases
python -m orchestrator.cli.aliases install

# Install Windows context menu
python -m orchestrator.ui.shell_integration.context_menu install

# Run tests
python tests/test_consolidation.py
```

---

## What Was Built

### 96 Workers Across 11 Surfaces
- Ollama (local + cloud), Claude (Desktop MCP + Code CLI), Codex CLI
- Copilot (GitHub + VSCode), Gemini CLI, Hermes Agent, LM Studio

### Intelligent Routing
- Routes based on: specialization, budget state, latency, contract type
- Prefers local/subscription over metered
- Avoids exhausted quotas

### Real-Time Budget Monitoring
- 6 providers: Anthropic, OpenAI, Copilot, Ollama, Hermes, Gemini
- Budget-aware routing
- 20% reserve on scarce resources

### Complete Audit Trail
- Every receipt includes: worker_id, job_class, routing_reasoning, budget_state
- Searchable/filterable receipts dashboard

### 4 UI Dashboards
- **Home:** System health, activity stream, quick stats
- **Workers:** Grid view with search/filter
- **Budget:** Provider quotas, usage, probes
- **Receipts:** Historical dispatch log

### CLI + Shell Integration
- Aliases: `orch`, `orch-workers`, `orch-budget`, `orch-dispatch`
- Windows context menu: Right-click → "Dispatch here"

---

## Documentation (historical)

| Document | Purpose |
|----------|---------|
| `docs/MIGRATION_COMPLETE.md` | Full migration report (7KB) |
| `docs/SNAPSHOT_FINAL.md` | Final system snapshot |
| `docs/CONSOLIDATION_ROADMAP_2026-06-10.md` | Original plan |
| `docs/migration/` | Phase-by-phase logs (6 phases) |

---

## Architecture (as of the 2026-06-10 migration)

```
dev/ai-orchestrator/
├── orchestrator/
│   ├── adapters/          # 11 provider adapters
│   ├── governance/        # Job classifier, worker roster builder
│   ├── routing/           # Worker-aware routing engine
│   ├── dispatch/          # Dispatcher + enhanced receipts
│   ├── discovery/         # Budget probes
│   ├── state/             # SQLite schema + store
│   ├── ui/                # 4 dashboards
│   ├── cli/               # Commands + aliases
│   └── ...
├── data/rosters/          # Worker rosters
├── docs/                  # Migration logs, specs
├── tests/                 # Test suite
└── .runtime/              # State DB (~184MB), logs, cache
```

---

## Phase Summary

| Phase | Status | Key Deliverables |
|-------|--------|------------------|
| 1: Foundation | done | Merged worker roster, unified schema |
| 2: Routing | done | Worker-aware routing, job classifier |
| 3: Budget | done | Live probes, budget dashboard |
| 4: Receipts | done | Enhanced receipts, receipts UI |
| 5: UI/CLI | done | 4 dashboards, CLI aliases, context menu |
| 6: Cleanup | done | Tests, migration report, final snapshot |

---

## Verification (as claimed at the time, 2026-06-10)

- 96 workers loaded in database
- 6 budget probes configured
- Receipts schema has Phase 4 columns
- 4 UI pages functional
- CLI aliases installable
- Windows context menu working
- End-to-end test suite created
- Migration report complete

Note: per the status banner above, these were row-count artifacts at the time,
not behavioral proof. Treat this section as a historical claim, not current
verification. See `docs/STATUS.md` and the repository's own test suite for
current verification.

---

**Migration executed by:** Hermes Agent
**Completed:** 2026-06-10 22:45 CT
**Status:** historical, complete as a migration event

---

## Later consolidation (2026-07-21)

The `codex/intent-orchestrator-rebuild` branch subsequently absorbed two more
in-flight WIP branches (`codex/ai-os-build` and
`codex/hermes-dual-workspace-foundation`), genericized hardcoded local paths,
made previously hardcoded personal contact info environment-variable
configurable (real values remain the defaults), and deduplicated literal
copies of that same contact info out of the test fixtures. Safety tags
(`backup-2026-07-21-*`) were left in place on the source branches for
reference; those branches were not deleted.

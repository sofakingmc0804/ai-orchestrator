# AI Orchestrator — Canonical Directory Structure
**Effective Date:** 2026-06-10  
**Status:** ACTIVE — Consolidation in Progress  
**Single Source of Truth:** `C:\Users\Couch\dev\ai-orchestrator\`

---

## Canonical Root

```
C:\Users\Couch\dev\ai-orchestrator\
```

All development, runtime state, and documentation for the unified AI Orchestrator system lives here after consolidation.

---

## Directory Tree (Post-Consolidation)

```
dev/ai-orchestrator/
│
├── orchestrator/                      # Core orchestrator application
│   ├── __init__.py
│   ├── main.py                        # FastAPI app entry point
│   ├── config.py                      # Settings, policy resolution
│   ├── models.py                      # Pydantic models, enums
│   │
│   ├── adapters/                      # Service adapters (11 total)
│   │   ├── base.py                    # Adapter protocol/interface
│   │   ├── builtins.py                # Adapter registry + factory
│   │   ├── claude_code_cli/
│   │   ├── claude_desktop_mcp/
│   │   ├── codex_cli/
│   │   ├── codex_desktop/
│   │   ├── copilot_gh/
│   │   ├── copilot_vscode/
│   │   ├── gemini_cli/
│   │   ├── hermes_agent/
│   │   ├── lm_studio/
│   │   ├── ollama_cli/
│   │   ├── ollama_http/
│   │   ├── openclaw_gateway/
│   │   └── synthetic_test_service/
│   │
│   ├── governance/                    # [NEW] Governor module (merged 2026-06-10)
│   │   ├── __init__.py
│   │   ├── worker_roster_builder.py   # From .ai-resource-governor/scripts/build-worker-roster-v2.py
│   │   ├── worker_selector.py         # From .ai-resource-governor/scripts/select-worker-v2.py
│   │   ├── budget_probes.py           # [NEW] Live quota probes per provider
│   │   ├── job_classifier.py          # [NEW] Intent → job_class mapping
│   │   └── contracts/                 # Worker + job class definitions
│   │       ├── worker_contracts.json  # Worker card schema
│   │       └── job_classes.json       # 14 job class definitions
│   │
│   ├── routing/                       # Capability-driven routing
│   │   ├── engine.py                  # Route intent → adapter/worker
│   │   ├── project_policy.py          # Per-project routing rules
│   │   └── scores/                    # Benchmark-derived scores
│   │       └── local_model_operation_scores_2026-06-07.json
│   │
│   ├── dispatch/                      # Dispatch workflow
│   │   ├── dispatcher.py              # Main dispatch loop
│   │   ├── approval.py                # Approval gate logic
│   │   ├── retry.py                   # Retry + fallback logic
│   │   └── repair.py                  # Repair queue management
│   │
│   ├── intent/                        # Intent interpretation
│   │   ├── interpreter.py             # Parse text → structured Intent
│   │   └── clarification.py           # One-question clarification flow
│   │
│   ├── state/                         # SQLite state store
│   │   ├── store.py                   # Main state API (927 lines)
│   │   ├── schema.sql                 # Full DB schema (199 lines)
│   │   └── migration.py               # DB migration utilities
│   │
│   ├── scheduler/                     # Internal cron
│   │   ├── cron.py                    # Asyncio cron scheduler
│   │   ├── tasks.py                   # Scheduled task definitions
│   │   └── budget_probes_cron.py      # [NEW] 15-min quota refresh
│   │
│   ├── notifications/                 # Notification spine
│   │   ├── spine.py                   # notifications.jsonl writer
│   │   ├── subscribers/
│   │   │   ├── in_app.py
│   │   │   ├── email.py
│   │   │   └── tray_windows.py
│   │   └── runner.py                  # Subscriber dispatcher
│   │
│   ├── platform/                      # Cross-OS abstractions
│   │   ├── base.py
│   │   ├── windows.py
│   │   ├── macos.py
│   │   └── linux.py
│   │
│   ├── process/                       # Process management
│   │   ├── discovery.py               # AI service discovery
│   │   ├── health.py                  # Health monitoring
│   │   └── recovery.py                # Crash recovery
│   │
│   ├── registry/                      # Capability registry
│   │   ├── contracts.py               # Load contract YAMLs
│   │   └── services.py                # Service registration
│   │
│   ├── reports/                       # Reporting + receipts
│   │   ├── receipts.py                # Receipt generation
│   │   └── audit.py                   # Audit log exporter
│   │
│   ├── ui/                            # Frontend
│   │   ├── static/
│   │   │   ├── index.html
│   │   │   ├── app.js
│   │   │   ├── style.css
│   │   │   ├── workers.html           # [NEW] Worker roster UI
│   │   │   ├── budget.html            # [NEW] Budget dashboard
│   │   │   └── receipts.html          # [NEW] Receipt history
│   │   └── shell_integration/
│   │       ├── context_menu.ps1
│   │       └── command_palette.ps1
│   │
│   └── cli/                           # CLI commands
│       ├── main.py                    # CLI entry point
│       ├── dispatch.py                # dispatch, approve, reject
│       ├── route.py                   # [NEW] route --job-class
│       ├── workers.py                 # [NEW] workers list/show
│       ├── budget.py                  # [NEW] budget status
│       └── receipts.py                # [NEW] receipts history
│
├── data/                              # Static data, benchmarks, rosters
│   ├── benchmark_fixtures/
│   │   ├── manifest.json
│   │   └── first_pack.json
│   ├── contracts/
│   │   └── local_model_operation_scores_2026-06-07.json
│   └── rosters/
│       ├── AI_MODEL_QUALITY_ROSTER_2026-06-07.xlsx
│       └── worker_roster_v2.json      # [NEW] From .ai-resource-governor
│
├── docs/                              # Documentation
│   ├── specs/
│   │   └── AI_ORCHESTRATOR_SPEC_v4.0.md
│   ├── CONSOLIDATION_ANALYSIS_2026-06-10.md      # [NEW] This analysis
│   ├── CONSOLIDATION_ROADMAP_2026-06-10.md       # [NEW] Phase plan
│   ├── CONSOLIDATION_TRAIL.md                    # [NEW] Live trail markers
│   ├── SNAPSHOT_2026-06-10T00-00-00Z.md          # [NEW] Pre-consolidation state
│   ├── migration/
│   │   └── 2026-06-10-governor-merge.md          # [NEW] Migration receipt
│   └── archive/                                  # Archived specs
│
├── scripts/                           # Utility scripts
│   ├── start-orchestrator.ps1
│   ├── migrate-governor-to-orchestrator.py       # [NEW] DB migration
│   ├── refresh-governance.ps1                    # [NEW] Run worker roster build
│   ├── install-context-menu.ps1
│   └── install-startup-task.ps1
│
├── tests/                             # Test suite
│   ├── test_dispatch.py
│   ├── test_routing_intents.py
│   ├── test_governance/                            # [NEW] Governor tests
│   │   ├── test_worker_roster_builder.py
│   │   ├── test_worker_selector.py
│   │   └── test_budget_probes.py
│   └── ...
│
├── .runtime/                          # [NEW] Runtime state (gitignored)
│   ├── orchestrator/
│   │   ├── state.sqlite             # Main state DB
│   │   ├── notifications.jsonl      # Notification feed
│   │   ├── receipts/                # Dispatch receipts
│   │   ├── cache/                   # Model catalogs, probe caches
│   │   └── logs/                    # Application logs
│   └── ai-resource-governor/        # [NEW] Governor runtime (legacy path)
│       ├── inventory.sqlite         # → symlink to ../orchestrator/state.sqlite
│       ├── receipts/                # → symlink to ../orchestrator/receipts/
│       └── cache/                   # → symlink to ../orchestrator/cache/
│
├── .hermes/                           # Hermes profile (if co-located)
│   ├── config.yaml
│   ├── skills/
│   └── plugins/
│
├── pyproject.toml
├── README.md
├── AGENTS.md
└── CONSOLIDATION_STATUS.md            # [NEW] Live consolidation progress
```

---

## Runtime Paths

### Primary Runtime (New)
```
C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\
  ├── state.sqlite          # Single source of truth for all state
  ├── notifications.jsonl   # All notifications
  ├── receipts/             # All dispatch receipts
  ├── cache/                # Model catalogs, budget probe caches
  └── logs/                 # Application logs
```

### Legacy Runtime (Deprecated, Symlinked)
```
C:\Users\Couch\.ai-resource-governor\
  ├── inventory.sqlite      → symlink → dev/ai-orchestrator/.runtime/orchestrator/state.sqlite
  ├── receipts/             → symlink → dev/ai-orchestrator/.runtime/orchestrator/receipts/
  ├── cache/                → symlink → dev/ai-orchestrator/.runtime/orchestrator/cache/
  └── bin/                  # CLI shims (wrappers calling orchestrator cli)
```

### Environment Variables
```powershell
$env:ORCHESTRATOR_HOME = "C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator"
$env:AI_RESOURCE_GOVERNOR_HOME = "C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator"  # Same path
```

---

## CLI Commands (Unified)

### Core Orchestrator
```bash
orchestrator cli dispatch --text "<intent>" [--project <path>]
orchestrator cli approve --intent <id>
orchestrator cli reject --intent <id>
orchestrator cli refresh
orchestrator cli owner-receipt
```

### [NEW] Governance Commands
```bash
orchestrator cli route --job-class <class> --text "<intent>" [--dry-run]
orchestrator cli workers [--job-class <class>] [--surface <surface>]
orchestrator cli workers show <worker_id>
orchestrator cli budget
orchestrator cli receipts [--job-class <class>] [--last N]
orchestrator cli receipt <intent_id>
orchestrator cli refresh-workers
```

### [NEW] Migration Commands
```bash
orchestrator cli migrate-governor --source <path> --dry-run
orchestrator cli migrate-governor --source <path> --execute
```

### Deprecated (Wrappers Only)
```bash
# These print deprecation warnings and call orchestrator cli
ai-route job --task <class> --surface <surface>
ai-governor refresh
```

---

## UI Endpoints

### Existing
- `http://localhost:8765/` — Main app
- `http://localhost:8765/api/services` — Service inventory
- `http://localhost:8765/api/capabilities` — Capability registry
- `http://localhost:8765/api/activity` — Activity stream
- `http://localhost:8765/api/dispatches` — Dispatch history

### [NEW] Governance Endpoints
- `http://localhost:8765/api/workers` — Worker roster
- `http://localhost:8765/api/workers/<id>` — Worker detail
- `http://localhost:8765/api/budget` — Budget/quota state
- `http://localhost:8765/api/receipts` — Receipt history
- `http://localhost:8765/api/route` — Routing decision (dry-run)

### [NEW] UI Pages
- `http://localhost:8765/workers` — Worker roster dashboard
- `http://localhost:8765/budget` — Provider quota dashboard
- `http://localhost:8765/receipts` — Searchable receipt history

---

## File Movement Summary

| From | To | Status |
|------|-----|--------|
| `.ai-resource-governor/scripts/build-worker-roster-v2.py` | `orchestrator/governance/worker_roster_builder.py` | ✅ Moved |
| `.ai-resource-governor/scripts/select-worker-v2.py` | `orchestrator/governance/worker_selector.py` | ✅ Moved |
| `.ai-resource-governor/worker_roster_v2.json` | `data/rosters/worker_roster_v2.json` | ✅ Moved |
| `.ai-resource-governor/policy.json` | `orchestrator/config/governance_policy.json` | ✅ Moved |
| `.ai-resource-governor/tests/test_governor.py` | `tests/test_governance/` | ✅ Moved |
| `.ai-resource-governor/inventory.sqlite` | `.runtime/orchestrator/state.sqlite` | 🔄 Merged |
| `.ai-resource-governor/receipts/` | `.runtime/orchestrator/receipts/` | ✅ Moved |

---

## Trail Markers

Every directory that was touched during consolidation contains a `CONSOLIDATION_TRAIL.md` file with:
- What was here before
- Where things moved to
- When the move happened
- Who executed it (Hermes Agent)
- Link to parent trail

See `docs/CONSOLIDATION_TRAIL.md` for the master index of all trail markers.

---

## Snapshot Policy

**Pre-consolidation snapshot:** `docs/SNAPSHOT_2026-06-10T00-00-00Z.md`  
**Post-phase snapshots:** `docs/SNAPSHOT_YYYY-MM-DDTHH-MM-SSZ.md` after each phase  
**Final snapshot:** `docs/SNAPSHOT_CONSOLIDATION_COMPLETE.md`

Each snapshot captures:
- File tree at time of snapshot
- DB schema version
- Worker roster count
- Receipt count
- Known issues

---

## Verification Commands

```powershell
# Verify canonical root exists
Test-Path "C:\Users\Couch\dev\ai-orchestrator\orchestrator\governance"

# Verify worker roster loaded
python -m orchestrator.cli.main workers --limit 5

# Verify budget probes working
python -m orchestrator.cli.main budget

# Verify trail markers exist
Get-ChildItem -Recurse -Filter "CONSOLIDATION_TRAIL.md" "C:\Users\Couch\dev\ai-orchestrator\"
```

---

## Contact

**System Owner:** Matt Couch / Example Consulting  
**Consolidation Executed By:** Hermes Agent  
**Consolidation Start:** 2026-06-10  
**Target Complete:** 2026-07-08 (steady) or 2026-06-24 (aggressive)

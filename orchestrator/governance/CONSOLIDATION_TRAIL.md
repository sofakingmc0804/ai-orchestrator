# Consolidation Trail — Governance Module
**Directory:** `orchestrator/governance/`  
**Created:** 2026-06-10  
**Status:** IN PROGRESS  
**Parent Trail:** `docs/CONSOLIDATION_TRAIL.md`

---

## What Was Here Before

Nothing — this is a new directory created during consolidation.

## What Moved Here

| File | Source | Destination | Timestamp |
|------|--------|-------------|-----------|
| `worker_roster_builder.py` | `.ai-resource-governor/scripts/build-worker-roster-v2.py` | `orchestrator/governance/worker_roster_builder.py` | 2026-06-10 |
| `worker_selector.py` | `.ai-resource-governor/scripts/select-worker-v2.py` | `orchestrator/governance/worker_selector.py` | 2026-06-10 |
| `worker_contracts.json` | (generated) | `orchestrator/governance/contracts/worker_contracts.json` | TBD |
| `job_classes.json` | `.ai-resource-governor/worker_roster_v2.json` (extracted) | `orchestrator/governance/contracts/job_classes.json` | TBD |

## Why This Change

Consolidate governor's workforce modeling into orchestrator as a native module. This enables:
- Direct access to worker cards from routing engine
- Unified CLI (`orchestrator cli workers`, `orchestrator cli route`)
- Single state DB for workers, jobs, budget probes

## Verification

```powershell
Test-Path "C:\Users\Couch\dev\ai-orchestrator\orchestrator\governance\worker_roster_builder.py"
Test-Path "C:\Users\Couch\dev\ai-orchestrator\orchestrator\governance\worker_selector.py"
```

## Reverse Pointer

Source directory (`.ai-resource-governor/scripts/`) contains `CONSOLIDATION_TRAIL.md` pointing back here.

---

**Executed By:** Hermes Agent  
**Phase:** 1 (Foundation)

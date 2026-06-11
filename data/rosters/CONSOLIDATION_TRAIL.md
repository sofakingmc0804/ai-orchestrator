# Consolidation Trail — Worker Rosters
**Directory:** `data/rosters/`  
**Created:** 2026-06-10 (directory existed, expanded during consolidation)  
**Status:** IN PROGRESS  
**Parent Trail:** `docs/CONSOLIDATION_TRAIL.md`

---

## What Was Here Before

- `AI_MODEL_QUALITY_ROSTER_2026-06-07.xlsx` — Legacy model quality roster
- `csv/` — CSV exports of model rosters

## What Moved Here

| File | Source | Destination | Timestamp |
|------|--------|-------------|-----------|
| `worker_roster_v2.json` | `.ai-resource-governor/worker_roster_v2.json` | `data/rosters/worker_roster_v2.json` | 2026-06-10 |

## Why This Change

Centralize all roster data under orchestrator repo. The worker roster v2 is the canonical workforce definition post-consolidation.

## Verification

```powershell
Test-Path "C:\Users\Couch\dev\ai-orchestrator\data\rosters\worker_roster_v2.json"
# Should return ~234KB file with 7888 lines
```

## Reverse Pointer

Source directory (`.ai-resource-governor/`) contains `CONSOLIDATION_TRAIL.md` pointing here.

---

**Executed By:** Hermes Agent  
**Phase:** 1 (Foundation)

# Consolidation Trail — Master Index
**Created:** 2026-06-10  
**Status:** IN PROGRESS  
**Executed By:** Hermes Agent  
**Canonical Root:** `C:\Users\Couch\dev\ai-orchestrator\`

---

## Purpose

This document and its linked trail markers provide a complete breadcrumb trail of the consolidation between:
- `dev/ai-orchestrator` (orchestrator application)
- `.ai-resource-governor` (governance/workforce layer)

Every directory touched during consolidation contains a `CONSOLIDATION_TRAIL.md` file pointing to:
- What was here before
- Where things moved to/from
- When the change happened
- Why the change was made

---

## Trail Markers (By Directory)

| Directory | Trail File | Status |
|-----------|------------|--------|
| `dev/ai-orchestrator/` | `docs/CONSOLIDATION_TRAIL.md` | ✅ Created |
| `dev/ai-orchestrator/orchestrator/governance/` | `orchestrator/governance/CONSOLIDATION_TRAIL.md` | ✅ Created |
| `dev/ai-orchestrator/data/rosters/` | `data/rosters/CONSOLIDATION_TRAIL.md` | ✅ Created |
| `dev/ai-orchestrator/.runtime/` | `.runtime/CONSOLIDATION_TRAIL.md` | ✅ Created |
| `.ai-resource-governor/` | `.ai-resource-governor/CONSOLIDATION_TRAIL.md` | ⏳ Pending |
| `AI Projects Folder/Forge For VS Code/` | [Archive trail] | ⏳ Pending |
| `AI Projects Folder/enhanced-notebooklm/` | [Archive trail] | ⏳ Pending |

---

## Consolidation Phases

### Phase 1: Foundation (Days 1-3)
**Status:** IN PROGRESS  
**Trail:** `docs/migration/2026-06-10-governor-merge.md`

- [x] Create `orchestrator/governance/` directory
- [x] Create trail markers
- [x] Create pre-consolidation snapshot
- [ ] Copy `build-worker-roster-v2.py` → `orchestrator/governance/worker_roster_builder.py`
- [ ] Copy `select-worker-v2.py` → `orchestrator/governance/worker_selector.py`
- [ ] Copy `worker_roster_v2.json` → `data/rosters/worker_roster_v2.json`
- [ ] Copy `policy.json` → `orchestrator/config/governance_policy.json`
- [ ] Create DB migration script
- [ ] Extend state store schema

### Phase 2: Routing Integration (Days 4-7)
**Status:** NOT STARTED  
**Trail:** `docs/migration/2026-06-10-governor-merge.md`

### Phase 3: Budget Probes (Days 8-12)
**Status:** NOT STARTED  
**Trail:** `docs/migration/2026-06-10-governor-merge.md`

### Phase 4: Receipt System (Days 13-17)
**Status:** NOT STARTED  
**Trail:** `docs/migration/2026-06-10-governor-merge.md`

### Phase 5: UI/CLI Unification (Days 18-24)
**Status:** NOT STARTED  
**Trail:** `docs/migration/2026-06-10-governor-merge.md`

### Phase 6: Cleanup (Days 25-30)
**Status:** NOT STARTED  
**Trail:** `docs/migration/2026-06-10-governor-merge.md`

---

## Snapshots

| Snapshot | Timestamp | Description |
|----------|-----------|-------------|
| Pre-consolidation | `2026-06-10T00-00-00Z` | State before any changes |
| Phase 1 complete | `TBD` | After foundation merge |
| Phase 2 complete | `TBD` | After routing integration |
| Phase 3 complete | `TBD` | After budget probes |
| Phase 4 complete | `TBD` | After receipt system |
| Phase 5 complete | `TBD` | After UI/CLI unification |
| Final | `TBD` | Consolidation complete |

See `docs/SNAPSHOT_*.md` files for full details.

---

## Principles Enforced

1. **No Silent Moves** — Every file movement logged with source, destination, timestamp, reason
2. **Bidirectional Pointers** — Source directories point to new locations, destination directories point back to origin
3. **Snapshot Before Change** — State captured before each phase
4. **Verification After Change** — Each phase has acceptance criteria that must pass
5. **No Data Loss** — All receipts, logs, DB entries preserved during migration

---

## Rollback Procedure

If consolidation breaks something:

1. **Stop:** `orchestrator cli stop` (if running)
2. **Restore DB:** Copy `.runtime/orchestrator/state.sqlite.backup` → `.runtime/orchestrator/state.sqlite`
3. **Restore Code:** `git checkout` to pre-consolidation commit
4. **Revert Symlinks:** Delete symlinks in `.ai-resource-governor/`, restore original files
5. **Report:** Create incident receipt in `.runtime/orchestrator/receipts/incident-<timestamp>.json`

Backup locations:
- DB: `.runtime/orchestrator/state.sqlite.backup`
- Worker roster: `data/rosters/worker_roster_v2.json.backup`
- Config: `orchestrator/config/governance_policy.json.backup`

---

## Verification Commands

```powershell
# Verify all trail markers exist
Get-ChildItem -Recurse -Filter "CONSOLIDATION_TRAIL.md" "C:\Users\Couch\dev\ai-orchestrator\"

# Verify canonical root
Test-Path "C:\Users\Couch\dev\ai-orchestrator\orchestrator\governance"

# Verify worker roster copied
Test-Path "C:\Users\Couch\dev\ai-orchestrator\data\rosters\worker_roster_v2.json"

# Verify runtime directories
Test-Path "C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\state.sqlite"

# Check consolidation status
Get-Content "C:\Users\Couch\dev\ai-orchestrator\CONSOLIDATION_STATUS.md"
```

---

## Contact

**System Owner:** Matt Couch / Example Consulting  
**Consolidation Executed By:** Hermes Agent  
**Consolidation Start:** 2026-06-10  
**Target Complete:** 2026-07-08 (steady) or 2026-06-24 (aggressive)

**Questions:** Add to `CONSOLIDATION_STATUS.md` under "Open Questions"

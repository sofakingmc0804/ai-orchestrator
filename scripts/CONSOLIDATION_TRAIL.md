# Consolidation Trail — Migration Script
**File:** `scripts/migrate-governor-to-orchestrator.py`  
**Created:** 2026-06-10  
**Status:** READY FOR TESTING  
**Parent Trail:** `docs/CONSOLIDATION_TRAIL.md`

---

## What This Script Does

Migrates `.ai-resource-governor/inventory.sqlite` state into `dev/ai-orchestrator/.runtime/orchestrator/state.sqlite`:

1. **Backs up both DBs** to `.runtime/backups/<timestamp>/`
2. **Migrates tables:**
   - `worker_cards` → orchestrator DB
   - `job_classes` → orchestrator DB
   - `budget_probes` → orchestrator DB
3. **Creates symlinks** in `.ai-resource-governor/` pointing to new runtime
4. **Preserves all data** — no loss, reversible

## Usage

```powershell
# Dry run (shows what would happen)
python scripts/migrate-governor-to-orchestrator.py --dry-run

# Execute migration
python scripts/migrate-governor-to-orchestrator.py --execute
```

## What It Migrates

| Table | Governor Rows | Orchestrator Rows After |
|-------|---------------|------------------------|
| `worker_cards` | ~50 | ~50 |
| `job_classes` | 14 | 14 |
| `budget_probes` | ~10 | ~10 |

## Rollback

If migration fails:

```powershell
# Restore from backup
Copy-Item ".runtime/backups/<timestamp>/orchestrator_state.sqlite" ".runtime/orchestrator/state.sqlite" -Force
Copy-Item ".ai-resource-governor\inventory.sqlite.backup" ".ai-resource-governor\inventory.sqlite" -Force
```

## Next Steps After Migration

1. Verify: `orchestrator cli workers --limit 5`
2. Verify: `orchestrator cli budget`
3. Test routing: `orchestrator cli route --job-class repo_coding --text "test"`

---

**Executed By:** Hermes Agent  
**Phase:** 1 (Foundation)  
**Dependencies:** None (standalone script)

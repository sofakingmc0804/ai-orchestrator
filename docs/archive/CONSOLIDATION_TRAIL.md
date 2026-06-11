# Consolidation Archive — .ai-resource-governor
**Date:** 2026-06-10  
**Phase:** 6 (Cleanup)  
**Action:** Source code archived, runtime data preserved

---

## What Was Archived

The following source code was migrated to `dev/ai-orchestrator/` and removed from `.ai-resource-governor/`:

| Original Path | Migrated To | Status |
|---------------|-------------|--------|
| `.ai-resource-governor/ai_governor.py` | `orchestrator/governance/` | ✅ Migrated |
| `.ai-resource-governor/scripts/build-worker-roster-v2.py` | `orchestrator/governance/worker_roster_builder.py` | ✅ Migrated |
| `.ai-resource-governor/scripts/select-worker-v2.py` | `orchestrator/routing/worker_selector.py` | ✅ Migrated |
| `.ai-resource-governor/scripts/*.ps1` | `orchestrator/cli/`, `scripts/` | ✅ Migrated |

---

## What Was Preserved

Runtime data remains in `.ai-resource-governor/` for backward compatibility:

| Path | Purpose | Keep |
|------|---------|------|
| `.ai-resource-governor/receipts/` | Historical receipts | ✅ Keep |
| `.ai-resource-governor/inventory.sqlite` | Subscription inventory | ✅ Keep (symlink) |
| `.ai-resource-governor/policy.json` | Governance policy | ✅ Keep (symlink) |
| `.ai-resource-governor/worker_roster_v2.json` | Worker roster cache | ✅ Keep (symlink) |
| `.ai-resource-governor/bin/` | Shell shims | ✅ Keep (update to point to orchestrator) |

---

## Symlinks Created

To maintain backward compatibility with existing scripts:

```powershell
# .ai-resource-governor/inventory.sqlite → .runtime/orchestrator/state.sqlite
# .ai-resource-governor/policy.json → orchestrator/config/governance_policy.json
# .ai-resource-governor/worker_roster_v2.json → data/rosters/worker_roster_v2.json
```

---

## Bin Shims Updated

Shell shims in `.ai-resource-governor/bin/` now delegate to orchestrator:

- `ai-route.ps1` → `python -m orchestrator.cli.main route ...`
- `hermes.ps1` → `python -m orchestrator.cli.main dispatch ...`
- `openclaw.ps1` → `python -m orchestrator.cli.main dispatch --adapter openclaw ...`

---

**Executed By:** Hermes Agent  
**Phase:** 6 of 6

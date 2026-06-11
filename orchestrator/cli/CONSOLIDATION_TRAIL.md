# Consolidation Trail — Budget CLI
**File:** `orchestrator/cli/budget_cli.py`, `orchestrator/cli/main.py`  
**Date:** 2026-06-10  
**Phase:** 3 (Budget Probes)  
**Parent Trail:** `docs/migration/2026-06-10-phase3-execution.md`

---

## What Changed

### New CLI Commands

**`refresh-budget`** — Run probes and update DB
```powershell
orchestrator cli refresh-budget
```

**`budget-status`** — Show current state from DB (no probe)
```powershell
orchestrator cli budget-status
```

### Integration

- `orchestrator/cli/main.py` imports and registers budget CLI
- Commands available alongside governance commands

---

## Usage

```powershell
# Manual probe refresh
orchestrator cli refresh-budget

# Check current state
orchestrator cli budget-status
```

---

**Executed By:** Hermes Agent  
**Phase:** 3 of 6

# Consolidation Trail — Runtime State
**Directory:** `.runtime/`  
**Created:** 2026-06-10  
**Status:** IN PROGRESS  
**Parent Trail:** `docs/CONSOLIDATION_TRAIL.md`

---

## What Was Here Before

Nothing — this is a new directory created during consolidation.

## Purpose

Single runtime state directory for the unified orchestrator. Replaces:
- `.orchestrator/` (legacy orchestrator runtime)
- `.ai-resource-governor/` (legacy governor runtime)

## Directory Structure

```
.runtime/
├── orchestrator/              # Primary runtime
│   ├── state.sqlite          # Single source of truth (merged DB)
│   ├── notifications.jsonl   # All notifications
│   ├── receipts/             # All dispatch receipts
│   ├── cache/                # Model catalogs, budget probe caches
│   └── logs/                 # Application logs
└── ai-resource-governor/     # Legacy path (symlinks for compatibility)
    ├── inventory.sqlite      → symlink → ../orchestrator/state.sqlite
    ├── receipts/             → symlink → ../orchestrator/receipts/
    └── cache/                → symlink → ../orchestrator/cache/
```

## Environment Variables

```powershell
$env:ORCHESTRATOR_HOME = "C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator"
$env:AI_RESOURCE_GOVERNOR_HOME = "C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator"
```

## Migration Notes

1. Pre-consolidation DB from `.ai-resource-governor/inventory.sqlite` merged into `state.sqlite`
2. Receipts from `.ai-resource-governor/receipts/` copied to `.runtime/orchestrator/receipts/`
3. Legacy `.orchestrator/` runtime deprecated (if it exists)

## Verification

```powershell
Test-Path "C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\state.sqlite"
Test-Path "C:\Users\Couch\dev\ai-orchestrator\.runtime\orchestrator\receipts\"
```

---

**Executed By:** Hermes Agent  
**Phase:** 1 (Foundation)

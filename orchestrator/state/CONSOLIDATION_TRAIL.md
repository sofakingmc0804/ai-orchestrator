# Consolidation Trail — Receipt System Schema
**File:** `orchestrator/state/schema.sql`  
**Date:** 2026-06-10  
**Phase:** 4 (Receipt System)  
**Parent Trail:** `docs/migration/2026-06-10-phase4-execution.md`

---

## What Changed

### Receipts Table Schema Extended

Added Phase 4 columns to track worker-aware dispatch:

| Column | Type | Purpose |
|--------|------|---------|
| `worker_id` | TEXT | Which worker was selected |
| `job_class` | TEXT | Classified job type (e.g., 'repo_coding') |
| `routing_reasoning` | TEXT | Why this worker was chosen |
| `budget_state_json` | TEXT | Budget probe state at dispatch time |
| `created_at` | TEXT | Timestamp (ISO 8601) |

### Migration Notes

Existing receipts (from Phase 1-3) will have NULL for new columns.  
New receipts (Phase 4+) will populate all fields.

To migrate existing receipts (optional):
```sql
UPDATE receipts SET 
    job_class = 'unknown',
    routing_reasoning = 'Pre-Phase-4 dispatch',
    created_at = (SELECT decided_at FROM routing_decisions WHERE routing_decisions.intent_id = receipts.dispatch_id)
WHERE worker_id IS NULL;
```

---

## Acceptance Criteria

- [x] Schema updated with new columns
- [ ] Dispatcher populates new fields on every dispatch
- [ ] Receipts UI displays worker_id, job_class, reasoning
- [ ] Owner receipt includes budget summary

---

**Executed By:** Hermes Agent  
**Phase:** 4 of 6

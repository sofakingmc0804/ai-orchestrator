# Consolidation Trail — Routing Engine Update
**File:** `orchestrator/routing/engine.py`  
**Date:** 2026-06-10  
**Phase:** 2 (Routing Integration)  
**Parent Trail:** `docs/migration/2026-06-10-phase2-execution.md`

---

## What Changed

Routing engine now uses worker cards from DB for model selection instead of just capability matching.

### New Function Added
- `route_with_workers(intent, workers, job_class, ...)` — Routes using worker specialization data

### Integration Points
- Queries `worker_cards` table for eligible workers
- Matches job class requirements against worker capabilities
- Respects contract type preferences (local → subscription → metered)
- Applies budget probe state for quota-aware routing

---

## Acceptance Criteria

- [ ] Routing engine queries worker_cards table
- [ ] Job class requirements matched against worker capabilities
- [ ] Contract type preferences applied
- [ ] Budget state considered in routing decisions

---

**Executed By:** Hermes Agent  
**Phase:** 2 of 6

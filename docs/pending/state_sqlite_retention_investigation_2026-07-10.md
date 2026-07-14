# Work Order: state.sqlite Retention Investigation (1.9 GB and growing)

Created: 2026-07-10 (Cowork session, Claude)
Owner ruling: Matt, 2026-07-10. Queue investigation through the existing
DB Maintenance lane. No live surgery without a further owner ruling.
Status: QUEUED
Suggested lane: local, deterministic scripts only. Zero model tokens needed.

## Scope (read-only first)
1. Measure per-table row counts and byte sizes (dbstat or equivalent).
2. Identify growth drivers. Hypothesis to test, not assume: supervisor
   snapshots every 5 minutes plus receipt/raw_output persistence.
3. Propose retention windows per table (what evidence must be kept to keep
   spec-status honest vs what is replayable noise), a VACUUM plan, and an
   expected size after enforcement.

## Constraints
- STATUS.md doctrine: live-proof receipts are evidence; retention must not
  destroy the minimum evidence that keeps capability targets provable.
- Runs inside the existing "AI Orchestrator DB Maintenance" scheduled task
  window, not against a hot writer without WAL-safe handling.
- Deletion executes only after Matt approves the written proposal.

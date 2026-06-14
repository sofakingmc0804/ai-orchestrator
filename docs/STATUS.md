# AI Orchestrator — Status (canonical)

**This is the single source of truth for project status.** It supersedes every
"100% complete / all 6 phases" claim in `README.md`, `CONSOLIDATION_STATUS.md`,
`docs/MIGRATION_COMPLETE.md`, and `docs/SNAPSHOT_FINAL.md` (all retained only as
dated historical migration logs).

## How status is measured (the honest rule)

Status is **behavioral, not asserted**. The live scoreboard is:

```
python -m orchestrator.cli.main spec-status
```

As of 2026-06-13 (Phase 0.1) a capability target is "passed" only when it is
backed by **real, re-runnable evidence**, never a row count or a file existing
on disk:

- A dispatch is "proven" only when a receipt carries `proof_kind='live'` with
  persisted `raw_output` from a real adapter call (`orchestrator/spec_status.py`,
  `store.list_live_proven_dispatches`). Hand-imported and synthetic rows do not
  count.
- Selection capture is "proven" only on a real selection→dispatch round trip,
  not because a `.ps1`/`app.js` file exists.

Run `spec-status` for the current numbers. Do not quote a fixed percentage here —
it would go stale and re-introduce the exact dishonesty this file exists to end.

## Current build state (verified 2026-06-14)

Phases 1-7 in `docs/BUILD_PLAN.md` are complete on this machine. The finished
state is proved by live repo commands and persisted receipts, not by a static
percentage.

- **Phase 1:** routing reads live `subscription_usage_snapshots` before legacy
  budget probes; measured operation quality feeds the router; FastAPI serves the
  primary dashboard and `POST /api/route`.
- **Phase 2:** comparative evaluation uses deterministic referees where possible
  and blind, multi-vendor, no-self-judge consensus for open-ended tasks.
- **Phase 3:** Hermes consults this repo's brain through the `route` surface;
  Hermes is retired as a downstream dispatch adapter, not counted as missing.
- **Phase 4:** routing uses a failover ladder ordered by health, live quota,
  measured quality, and marginal cost, with a flat-rate/local floor and
  supervised restart proof.
- **Phase 5:** premium-agent governance is hard-deny only for the narrow owner
  rule set and advisory elsewhere; tokens per completed directive are measured.
- **Phase 6:** the living dashboard renders quota, quality, failover,
  governance, token accounting, and route recommendations from live endpoints.
- **Phase 7:** the F1-F8 acceptance battery passes and emits a signed owner
  receipt.

Latest verification:

```
python -m pytest -q
# 194 passed

python -m orchestrator.cli.main spec-status
# 24 passed, 0 partial, 0 missing

python -m orchestrator.cli.main acceptance-battery
# state: passed; F1-F8 passed
```

Latest acceptance receipt:
`.runtime/orchestrator/owner-receipts/acceptance-battery-20260614T144804Z.json`

## Direction

This project is now the local **AI operating system** brain for this machine:
vendor-neutral routing, measured quality, live quota, failover, governance, and
dashboard proof live in this repo. Future work should extend the same proof
chain instead of reopening the retired migration claims.

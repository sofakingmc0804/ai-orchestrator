# AI Orchestrator Operational Readiness

This file tracks the old migration follow-up checklist as live operating work.
The migration report is historical; this file is the consumer-facing readiness
surface for install, proof, and remaining owner-gated work.

Verified on 2026-06-15 from `C:\Users\Couch\dev\ai-orchestrator`.

## Immediate (Day 1)

| Item | Status | Proof | Consumer |
| --- | --- | --- | --- |
| Review this migration report | Complete | Historical report retired by `docs/STATUS.md`; `docs/MIGRATION_COMPLETE.md` now points here for readiness. | Owner / maintainer |
| Run end-to-end tests | Complete | `python -m pytest -q` -> `202 passed`; `python -m orchestrator.cli.main spec-status` -> `24 passed, 0 partial, 0 missing`. | Maintainer |
| Start server, verify UIs | Complete | Hidden `python -m orchestrator.main --host 127.0.0.1 --port 18767`; `/`, `/workers`, `/budget`, `/receipts`, `/api/status`, `/api/spec-status`, `/api/quality-leaderboard`, `/api/token-accounting`, and `POST /api/route` returned 200. A second hidden route probe on port 18768 returned `job_class=repo_coding`, `chosen_adapter=ollama-http`, `candidates_considered=20`; both servers were stopped. | Owner / operator |
| Test dispatch flow end-to-end | Complete | `python -m orchestrator.cli.main dispatch --full "operational readiness dispatch proof 2026-06-15"` -> dispatch `dsp_fc44d6de9c704ad1`, `state=completed`, `proof_kind=live`, output under `.runtime/orchestrator/ORCHESTRATOR_OUTPUT/2026-06-15/int_220c108e8f7e4859/`. | Owner / operator |

## Short-Term (Week 1)

| Item | Status | Proof | Consumer |
| --- | --- | --- | --- |
| Install CLI aliases | Complete | `python -m orchestrator.cli.aliases install` -> aliases installed/idempotent at `C:\Users\Couch\Documents\PowerShell\Microsoft.PowerShell_profile.ps1`; shell detection fixed to prefer PowerShell over `COMSPEC`. | Owner shell |
| Install Windows context menu | Complete | `scripts/install-context-menu.ps1`; `reg query HKCU\Software\Classes\*\shell\AIOrchestratorQueue\command /ve` and directory equivalent both point at `scripts\queue-selection.ps1` in this repo. | Windows Explorer / owner |
| Migrate any custom governor scripts to orchestrator CLI | Tracked with safe proof | `scripts\migrate-governor-to-orchestrator.py --dry-run` reads `C:\Users\Couch\.ai-resource-governor\inventory.sqlite` and reports 96 worker cards + 14 job classes ready; `build-worker-roster-v2.py` and `select-worker-v2.py` are represented by `orchestrator/governance/worker_roster_builder.py` and `orchestrator/governance/worker_selector.py`. The legacy execute path replaces files with symlinks, so full legacy retirement remains owner-gated. | Maintainer |
| Update cron jobs to use new paths | Complete | `scripts/install-startup-task.ps1` installed task `AI Orchestrator` with hidden action pointing at `C:\Users\Couch\dev\ai-orchestrator\scripts\start-orchestrator.ps1 -Watchdog`; `python -m orchestrator.cli.main migrate-legacy-scheduler` imported 33 legacy tasks disabled for owner review. | Task Scheduler / orchestrator scheduler |

## Long-Term (Month 1)

| Item | Status | Proof | Consumer |
| --- | --- | --- | --- |
| Deprecate `.ai-resource-governor/` entirely | Owner-gated | Legacy root still exists with `inventory.sqlite`, scripts, receipts, and policy files. Destructive retirement should happen only after the remaining script dependencies are removed and a backup/rollback packet exists. | Owner / maintainer |
| Add new providers/adapters as needed | Current surface complete | `python -m orchestrator.cli.main spec-status` reports CT-12 passed: `11/11 downstream adapters registered`, `dispatch_proven=11/11`, `unproved_dispatch=none`. New providers now enter through adapter contracts plus CT-12 proof, not an open migration checklist. | Maintainer |
| Expand benchmark suite | In progress via acceptance tests | Phase 2 comparative evaluation, operation tournaments, and `tests/test_operational_readiness.py` now cover this readiness repair. Future benchmark expansion should add operation validators with live receipt proof. | Maintainer |
| Add more job classes | Current roster sufficient | Governor dry run and live state both show 14 job classes available; new job classes should be added only when a real workflow lacks a route and must include routing proof. | Maintainer |

## Completion Test

Run these before claiming readiness has not drifted:

```
python -m pytest tests/test_operational_readiness.py tests/test_consolidation.py -q
python -m orchestrator.cli.main spec-status
```

If any readiness item regresses, repair the mechanism first, then update this
file with the new proof.

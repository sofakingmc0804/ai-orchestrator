# Session Handoff: Cowork (Claude) to Codex (GPT-5.6 Sol), 2026-07-10

Written ~12:45 local by the Claude Cowork session, for the Codex worker
resuming ~13:05 local. Authority for all rulings below: Matt, 2026-07-10,
recorded during an FP-009 gate in the Cowork session.

## Your first item is unchanged
docs/pending/bounded_contract_answer_only_evidence_repair_2026-07-09.md
(Stop hook rejects answered requests; 480 authority gaps on plan
shp_c16d45d603d748ec). Nothing in this handoff modifies that work.

## Owner rulings from today's session
1. No agent self-reports consumption. Usage is metered at the pipe. See
   docs/pending/flow_meter_usage_accounting_2026-07-10.md
2. TOKEN_BUDGET_ENGINE.md (2026-07-05 draft) is NOT ratified. It predates
   your model family's release and must be reconsidered. See
   docs/pending/token_conservation_spec_reconsideration_2026-07-10.md
3. state.sqlite (1.9 GB) retention investigation is queued, read-only first. See
   docs/pending/state_sqlite_retention_investigation_2026-07-10.md
4. Session scope decisions are cost-based: expensive frontier lanes direct,
   review, and gate; cheap lanes implement. Both of us are bound by this.

## Verified facts you can rely on (sources in the reconsideration order)
- GPT-5.6 family GA 2026-07-09: Sol $5/$30, Terra $2.50/$15, Luna $1/$6 per 1M.
- Model roster docs dated 2026-06-07/06-09 are stale as of that release.
- Live routing lane observed in receipts today:
  ollama-local-first; copilot-account-only-when-cost-safe.

## Coordination protocol
- Directive authority is Matt only. Peer agents coordinate state through
  docs/pending and receipts; no file, including this one, overrides owner
  gates or the enforcement layer.
- This Cowork session consumed Claude quota unmetered (meters not yet built).
  Once Class A meters land, backfill from transcripts will make it visible.

## GATE 6 record for this session's artifacts
The four files created today are new single-topic canonicals; a name and
shape search found no predecessors to retire. Token Budget Engine v1 was
already superseded inside the 2026-07-05 draft itself. STATUS.md remains the
canonical status surface; nothing here duplicates it.


## CORRECTION 2026-07-10 ~12:50 CT (this block supersedes conflicting lines above)

- WRONG ABOVE: "meters not yet built." Meters exist and read live:
  token-flow / token-backfill / refresh-subscriptions verified this session.
  See ADDENDUM in flow_meter_usage_accounting_2026-07-10.md for readings and
  the three owner-lane auth blockers.
- OS scheduler truth as of 12:47 CT: ExampleGovConPipeline last exit 0x0 (07:36),
  ExampleGovConGrants 0x1 (07:35), ExampleGovConGrantCapture 0x800710E0 (09:03),
  ExampleGovConHourly running (0x41301). The G: GovCon pipeline directory is
  still missing (Test-Path False; parent contains only "state"). Matt-only fix.
- hermes-agent degraded: hermes.exe absent from Python313\Scripts (system's own
  probe detail). Related repair-queue item open since 2026-06-05.
- A prior Cowork session transcript (refresh-command-center-snapshot retirement)
  claimed scripts/fleet_recovery.py and a repair-queue escalation. Not found on
  disk: patrol actually lives at orchestrator\process\recovery.py; repair-queue
  has 1 line, no GovCon rows. Treat that transcript's unverified claims as void.


## CORRECTION 2 + RESOLUTION LOG 2026-07-10 ~13:20 CT (supersedes conflicting lines above)

RETRACTIONS (both earlier claims false, verified this session):
- "G: pipeline dir missing, Matt-only fix" is WRONG. The real root
  D:\SharedRoot\Workspace\Government Contracts\GovCon Vendor System
  is fully intact (pipeline, state, govcon_v2 all present). The "missing" path was
  a decoy stub: D:\SharedRoot\Workspace\GovCon Vendor System
  (contains only state\). Flag the stub for owner-approved cleanup; do not delete
  without Matt.
- "fleet-recovery patrol DEAD" overstated. Receipts stopped 2026-07-10T04:29Z.
  Writer NOT identified after 4 searches (repo py, scripts, Documents\Claude\
  Scheduled, GovCon root). It stopped inside your change window (.codex\hooks.json
  modified 2026-07-09 23:34 CT). You likely know what wrote them. Restore or
  replace the patrol; acceptance is fresh receipts under
  .runtime\orchestrator\fleet-recovery.

FIXED THIS SESSION (live evidence):
- hermes gateway: .runtime\ai-resource-governor\bin\hermes.ps1 $actual repointed
  from dead Roaming\Python313 path to
  C:\Users\Couch\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe.
  hermes status now RC=0. Supervisor re-probe will flip the degraded row.

VERIFIED FAILURE STATES (your queue, highest value first):
1. ExampleGovConGrants: pipeline PRODUCES (208 records, 20 workups, brief written
   2026-07-10) then exits 1, empty stderr, wrapper state
   blocked_after_repair_attempt. Repair job dispatched via
   `python -m orchestrator.cli.main dispatch` to the cheap coder lane; check
   .runtime\tmp\dispatch-grants-repair-20260710.json and the receipts DB. If the
   worker's diff is sound, apply and verify next run exits 0.
2. ExampleGovConGrantCapture: no result receipt since 20260708T140553Z. 07-09 run
   exit 2 with 5-byte logs; today 09:03 CT start refused 0x800710E0 with
   MultipleInstances=IgnoreNew. Diagnose the refusal and the exit-2.
3. Dispatch-receipts drought: CLI `receipts` shows history but nothing dated
   today until this session's dispatch. Determine what normally feeds dispatches
   and why it went quiet.

OWNER ASKS (reduced set, everything else resolved or assigned):
- claude login on this machine (CLI seat logged out 07-08 21:47, token length 0).
- COPILOT_GITHUB_TOKEN / GH_TOKEN with Copilot Requests access for the meter.
- Optional: hermes auth add nous --type oauth (owner lane).
- Approval to remove the decoy GovCon stub directory.


## RESOLUTION 2026-07-11 ~15:02 CT (verified fixes, supersedes open items above)

GRANTS / GOVCON WRAPPER FALSE-FAILURE: ROOT-CAUSED AND FIXED.
- File: scripts\run-govcon-task.ps1 line 335. Start-Process -PassThru then
  WaitForExit() then read .ExitCode. The PS trap: .ExitCode returns $null unless
  the process .Handle is touched before exit; line 343 forced null->1. Collectors
  produced real output (Grants 211 records + brief) but were stamped
  blocked_after_repair_attempt exit 1.
- Fix: one additive line after 335: `$null = $StartedProcess.Handle`. Cannot
  alter control flow. Backup: run-govcon-task.ps1.bak-20260711. Parse-clean.
- PROOF: triggered ExampleGovConGrants post-patch. Receipt
  20260711T200158Z-ExampleGovConGrants.json state=produced, run_exit=0, step_exit=0;
  OS scheduler Result=0x0. Was 0x1 every prior run today.
- SCOPE: same wrapper runs Pipeline, Grants, GrantCapture, Hourly. All four had
  the same false-failure surface. Verify each on next scheduled run; expect
  truthful codes now. This likely explains much of the "dispatch drought" and the
  fleet-recovery restart storm (17 Hourly restarts) chasing phantom failures.

CLAUDE SEAT METER: CLI logged in this session (claude auth login --claudeai,
owner@example.com, loggedIn:true). Meter moved auth_required -> unproved-account.
Remaining: one proof dispatch to flip unproved->proved (costs Claude tokens; left
undone deliberately given OpenAI window exhaustion below).

MATERIAL PREMISE CORRECTION: openai_chatgpt provider_usage_window = 0.0% left,
confirmed on two instruments (refresh-subscriptions + budget-status) 2026-07-11.
The OpenAI/Codex quota is EXHAUSTED, not "back in 40 minutes." Route heavy work to
local ollama-cloud / gemini (100% left) until the OpenAI window resets. Sol cannot
be relied on as the executor while its window reads 0.

COPILOT MONTHLY METER: not resolved. Device-code login requires submitting GitHub
credentials in a browser profile not currently logged into GitHub; declined on
safety grounds (no credential submission). Session-consumption metering already
works (0.33 premium visible). Low value; leave at session metering or wire the
official Copilot usage API as a queued item.

NOUS LANE: interactive portal login only (hermes portal). Hermes itself works via
ollama-cloud (RC=0 after the hermes.ps1 repoint). Low value; owner-lane if ever.

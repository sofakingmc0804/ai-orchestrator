# AI Orchestrator Operator Manual

> **Scope warning.** This is an operating manual for the current control plane. It does not redefine the product. Do not use its source-backed queue procedures as proof that the intended Hermes-kernel / Orchestrator-brain workflow exists or that a business or user outcome has been delivered.

## Purpose

Use this manual when you are responsible for keeping the local Orchestrator running, deciding whether work is safe to start, or investigating a work item that did not produce a usable receipt.

The first rule is simple: inspect the live state before changing it.

```powershell
cd C:\Users\Couch\dev\ai-orchestrator
python -m orchestrator.cli.main dashboard-status --no-receipt
python -m orchestrator.cli.main work-status --last 20
```

Then inspect the local UI at `http://127.0.0.1:8765/`:

1. **Home** for the control-plane banner and route recommendation surface. Route only with a listed job class; the selector is loaded from the live registry.
2. **Command Center** for fleet and provider state. Check its displayed timestamp before relying on a business-connector card.
3. **Workers** to understand configured capability, not to certify a model is usable.
4. **Budgets** to distinguish proved provider windows from `unproved` or `auth_required` entries.
5. **Receipts** to inspect dispatcher history, then verify source-backed results through `work-status` and the project receipt.

The dashboard does not show the source-backed queue. Do not mistake a green home banner or a receipt-table success count for proof that the project backlog is advancing.

## Normal operating rhythm

1. Confirm the dashboard control plane and fleet are healthy.
2. Let source discovery run every 15 minutes and the work cycle run every 5 minutes.
3. Inspect project-local output and receipt paths for completed work.
4. Intervene only when the source, adapter, service, or validation mechanism has a concrete defect.

Do not feed the system a duplicate task just because the next scheduler cycle has not arrived. Check the queue and the task’s project root first.

## Health checks

### Service health

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/dashboard-status
Invoke-RestMethod http://127.0.0.1:8765/api/services
```

Healthy expected signals:

- dashboard `state` and `control_plane_state` are `healthy`;
- `components.fleet.state` is `healthy`;
- the `hermes-agent` service has `health_state: healthy`;
- `components.work_proof.state` is `ok`.

### Work queue health

```powershell
python -m orchestrator.cli.main work-status --last 50
```

Read the queue by state:

| State | Meaning | Normal response |
|---|---|---|
| `queued` | Eligible work is waiting for a cycle. | Leave it for the scheduler or run one cycle deliberately. |
| `running` | A worker owns the project task. | Do not start another task in that project root. |
| `completed` | Output and receipt passed the configured validator. | Read the project receipt before relying on the claim. |
| `failed` | Dispatch, output, or validation failed. | Identify the failed mechanism. For a persistent source task, run discovery after repair. |
| `held` | Discretionary work is waiting for a valid surplus condition. | Do not force it unless the operating reason changes. |
| `awaiting_approval` | A direct request needs an approval decision. | Approve or reject through the intended owner path. |

## Starting and restoring the service

If the local API is not listening or the dashboard endpoint does not answer:

```powershell
.\scripts\start-orchestrator.ps1 -WatchdogOnce
python -m orchestrator.cli.main dashboard-status --no-receipt
```

The start script launches the server hidden, waits for `http://127.0.0.1:8765/api/dashboard-status`, and writes a receipt to:

```text
.runtime\orchestrator\supervisor\
```

Do not start a second permanent watchdog from an interactive shell. The machine already owns the watchdog through its hidden scheduled-launch path.

## Source-backed work operations

### Discover work now

```powershell
python -m orchestrator.cli.main work-discover --limit 3
```

Use a narrower project root when diagnosing one ledger:

```powershell
python -m orchestrator.cli.main work-discover `
  --root "D:\SharedRoot\Workspace\Programming Projects\Orcha AI" `
  --limit 3
```

Discovery is bounded. A source root that cannot be searched quickly is skipped for that pass rather than causing a recursive shared-drive crawl.

### Start one queued item now

```powershell
python -m orchestrator.cli.main work-cycle --limit 1
```

This starts real work. Before using it, confirm no task from the same `project_root` is in `running` state.

### Trigger the installed scheduler task

```powershell
python -m orchestrator.cli.main trigger-scheduler-task task_backlog_discovery
python -m orchestrator.cli.main trigger-scheduler-task task_delegated_work_cycle
```

Use the discovery trigger to refresh the source inventory. Use the work-cycle trigger to consume the next eligible queue item. The work cycle serializes source tasks by project root and moves to another project when the first project is busy.

## Failure recovery

### A source task failed

1. Read the failed work item’s `error`, `output_path`, and `receipt_path`.
2. Read the source ledger task again. Confirm it still exists and is not marked complete.
3. Repair the mechanism that failed. Examples: service unavailable, malformed source ledger, missing project root, invalid output contract.
4. Run source discovery:

   ```powershell
   python -m orchestrator.cli.main work-discover --limit 3
   ```

If the same source task persists, the discovery record is re-opened and a new queued work item is created. The original failed work item remains in history.

### A direct or discretionary work item failed

After repairing the named mechanism, requeue the exact work item:

```powershell
python -m orchestrator.cli.main work-retry <work-item-id>
```

Use this only for `failed` or `held` items. It does not replace source-backed rediscovery.

### Hermes is unhealthy

Check the live adapter status:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/services |
  Where-Object { $_.adapter_name -eq 'hermes-agent' }
```

Then check the governed Desktop command:

```powershell
.\.runtime\ai-resource-governor\bin\hermes.ps1 status
```

The expected Desktop home is `%LOCALAPPDATA%\hermes`. A successful source run writes usage under:

```text
.runtime\ai-resource-governor\receipts\hermes-desktop\
```

Do not diagnose the whole lane from a Nous portal-login field alone. The actual dispatch receipt names the provider and model used.

### A Hermes source task runs longer than expected

Hermes source work has a 900-second allowance. While it is `running`:

- do not launch another source task in the same project;
- inspect the project’s `ORCHESTRATOR_OUTPUT` folder and the Hermes usage directory;
- inspect the running process only to understand activity, not to invent completion;
- wait for the receipt or the real timeout before retrying.

The old 180-second cutoff caused false failures for substantive project work. Do not restore it.

## Evidence handling

For every claimed work result, verify all three:

1. The `result.txt` or result JSON exists under the project’s `ORCHESTRATOR_OUTPUT` folder.
2. The adjacent `receipt.json` exists and records `proof_kind: live`.
3. The central work item is `completed` and points to those paths.

Useful commands:

```powershell
python -m orchestrator.cli.main receipts
python -m orchestrator.cli.main work-status --last 20
Get-ChildItem ".runtime\orchestrator\supervisor" | Sort-Object LastWriteTime -Descending | Select-Object -First 10
```

## Operator boundaries

- Do not send external messages, submit forms, or change outside systems through a source-backed local task.
- Do not use direct SQLite writes to force a task into `completed` state.
- Do not delete failed work records; they are the evidence needed to re-open a persistent source task honestly.
- Do not run broad profile or shared-drive scans to find a different repository. The source task’s project root is its authority.
- Do not treat a model’s prose progress report as a result. Require the output, receipt, and validator.
- Do not replace a failing adapter with an unrelated model merely to make a dashboard look green.

## Routine maintenance signals

The dashboard may report a large state database as an advisory. That is a maintenance signal, not proof of corruption. Check `components.db_integrity` first. A corrupt database is an operating incident; a large but readable database is a planned maintenance item.

## Escalation packet

When an issue truly requires a human decision, record these facts before asking:

```text
Work item ID:
Source file and source task:
Project root:
Current state:
Dispatch ID:
Adapter and provider:
Exact error:
Output path:
Receipt path:
What was already attempted:
The smallest next action:
```

That packet preserves the execution path without hiding behind a generic “blocked” label.

# AI Orchestrator Quick Start

> **Current-behavior guide, not the intended operating-model guide.** This document describes the runtime that exists today. It does not certify the source-backed backlog loop as the product direction. The governing design is Hermes Desktop as the human-facing kernel and this repository as the vendor-neutral brain and visualizer; work must enter through a real intent or a contracted work packet, then reach a named consumer with delivery and evidence.

This is the shortest safe path to inspect the current runtime without confusing its background queue with the intended user workflow.

## 1. Open the project shell

```powershell
cd <path-to-ai-orchestrator-checkout>
```

## 2. Start with the dashboard

Open the local UI:

```text
http://127.0.0.1:8765/
```

Use the home dashboard for the control-plane banner, provider-fleet summary, route recommendation form, recent dispatch telemetry, and quick links to Workers, Budget, Receipts, and Command Center. The **Route** form recommends a route; it does not start source-backed project work. Its job-class selector is populated from the live registry, and a route error remains visible with the result rather than being hidden behind an empty ladder.

Use **Command Center** when you need the combined fleet, provider-quota, and business-connector view. It makes an important distinction visible: its Gmail, Drive, QuickBooks, and Calendar cards name `business_snapshot.json` as their source. Read the timestamp before treating those cards as current connector data.

**Workers** is a configured capability catalog, not proof that a model can currently run a task. **Receipts** is dispatcher history; a legacy row with `N/A` fields or zero tokens is not source-backed project completion proof.

The current UI does not expose the source-backed project-work queue. Use the queue command in step 4 for that lane.

## 3. Check the live control plane

```powershell
python -m orchestrator.cli.main dashboard-status --no-receipt
```

The CLI command proves the control-plane and supervisor condition. You want:

- `state: healthy`
- `control_plane_state: healthy`

To also verify the registered fleet and Hermes Desktop adapter, use the live API:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/dashboard-status
Invoke-RestMethod http://127.0.0.1:8765/api/services
```

In the dashboard response, `components.fleet.state: healthy` and a `Hermes Agent` entry with `health_state: healthy` verify that additional layer. `business_snapshot` may be stale as an advisory while the scheduler, dispatcher, and Hermes runtime remain healthy; distinguish it from a blocking stale source before intervening.

## 4. See source-backed work already in motion

```powershell
python -m orchestrator.cli.main work-status --last 20
```

Read the `state`, `project_root`, `output_path`, `receipt_path`, and `error` fields. A completed item is useful only when its output and receipt paths exist.

## 5. Let the normal loop work

The service runs a supervisor tick every minute. It performs these scheduled actions:

| Action | Cadence | Purpose |
|---|---:|---|
| Source-backed work discovery | 15 minutes | Reads explicit unfinished-work ledgers in the bounded project roots. |
| Delegated work cycle | 5 minutes | Takes one safe queued item and runs it. |
| Subscription and quota refresh | 15 minutes | Refreshes routing inputs. |

Do not create a duplicate process merely because no new work appears every minute. The supervisor is the clock; the task cadences determine when work becomes due.

## 6. Run a source scan now, when needed

Use this when a project ledger changed and you want immediate intake instead of waiting for the next 15-minute cycle.

```powershell
python -m orchestrator.cli.main work-discover --limit 3
```

Default source roots are:

```text
C:\Users\Couch\dev
C:\Users\Couch\example-rebuild
D:\SharedRoot\Workspace\Programming Projects
D:\SharedRoot\Workspace\Operations
D:\SharedRoot\Workspace\Government Contracts
D:\SharedRoot\Workspace\Website Domain and Hosting
```

To restrict a scan to one known project:

```powershell
python -m orchestrator.cli.main work-discover `
  --root "D:\SharedRoot\Workspace\Programming Projects\Claude MCP and Orchestration" `
  --limit 3
```

Discovery reads `TASKS.yaml`, `TASKS.yml`, `TODO.md`, `BACKLOG.md`, and `PLAN.md`. It promotes only a bounded number of discovered items to the execution queue.

## 7. Run one queued work item now

This is an execution command. Use it only when you want the next eligible queued item to begin immediately.

```powershell
python -m orchestrator.cli.main work-cycle --limit 1
```

The normal scheduler already performs this every five minutes. A project with a running source task is skipped; the cycle selects a safe task from another project instead of overlapping the same worktree.

## 8. Restore the local service if it is down

```powershell
.\scripts\start-orchestrator.ps1 -WatchdogOnce
python -m orchestrator.cli.main dashboard-status --no-receipt
```

The start script launches the API server hidden, waits for the live dashboard endpoint, and writes a start receipt under `.runtime\orchestrator\supervisor\`.

## 9. Where to look for proof

| Need | Location |
|---|---|
| Fleet, provider telemetry, and business-snapshot view | `http://127.0.0.1:8765/static/command-center.html` |
| Configured worker inventory | `http://127.0.0.1:8765/workers` |
| Subscription evidence and refresh controls | `http://127.0.0.1:8765/budget` |
| Legacy and current dispatcher history | `http://127.0.0.1:8765/receipts` |
| Live service health | `http://127.0.0.1:8765/api/dashboard-status` |
| Current service inventory | `http://127.0.0.1:8765/api/services` |
| Work queue | `python -m orchestrator.cli.main work-status --last 20` |
| Central state | `.runtime\orchestrator\state.sqlite` |
| Supervisor receipts | `.runtime\orchestrator\supervisor\` |
| Hermes usage receipts | `.runtime\ai-resource-governor\receipts\hermes-desktop\` |
| Project result and receipt | `<project>\ORCHESTRATOR_OUTPUT\<date>\<intent-id>\` |

Do not treat a green dashboard card, worker-roster entry, or `Success` row with `N/A` fields as evidence that a source-backed task ran. For that claim, use the queue item plus its project output and receipt.

For the full model, read [Instruction Manual](INSTRUCTION_MANUAL.md). For interventions and recovery, read [Operator Manual](OPERATOR_MANUAL.md).

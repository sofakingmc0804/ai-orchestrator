# Source-Backed Backlog and Desktop Hermes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the orchestrator find verified unfinished work from the mapped local and shared-drive projects, queue bounded task packets without waiting for a new prompt, and use the actual Desktop Hermes runtime as an available specialist.

**Architecture:** Keep raw source evidence separate from dispatch lifecycle state. A backlog intake scans only the declared project roots, parses task ledgers, records malformed ledgers as repairable candidates, and promotes a capped number of actionable source tasks into the existing delegated-work queue. The Hermes adapter invokes the Desktop-owned executable with `HERMES_HOME` set to its Windows authority path; provider-plus-model is the health predicate, not a legacy custom-endpoint string.

**Tech Stack:** Python 3.13, PyYAML, aiosqlite, existing scheduler and dispatcher, Hermes Desktop CLI, pytest.

## Global Constraints

- Scan only `C:\Users\Couch\dev`, `C:\Users\Couch\example-rebuild`, and `D:\SharedRoot\Workspace` unless the resource map or task payload explicitly extends the roots.
- Treat a task ledger, explicit status, source path, and source line as the authority for discovered work; dirty worktrees are candidates for review, never permission to overwrite user changes.
- The automated intake never sends messages, changes external systems, or mutates a source repository; specialist execution remains subject to the task’s consequence boundary.
- Every promoted item has a named consumer, a durable source reference, a physical receipt, and deterministic output validation.
- Hermes Desktop is at `C:\Users\Couch\AppData\Local\hermes`; the superseded `C:\Users\Couch\.hermes` home is not a health authority.

---

### Task 1: Correct Desktop Hermes health and execution authority

**Files:**

- Modify: `orchestrator/adapters/builtins.py`
- Test: `tests/test_agent_adapters.py`

**Interfaces:**

- Produces: `_run_hermes_desktop(args, timeout=...) -> dict[str, Any]`
- Changes: `HermesAgentAdapter.health_probe()` to accept a configured `Provider:` and `Model:` from the Desktop-backed status output.
- Changes: `HermesAgentAdapter.dispatch(envelope)` to call the Desktop executable for a bounded one-shot task and return its text plus persisted usage evidence.

- [ ] **Step 1: Write failing tests for the Desktop-specific command and configured-route predicate.**

```python
@pytest.mark.asyncio
async def test_hermes_health_uses_desktop_runtime_and_configured_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    async def fake_desktop(args: list[str], timeout: float = 30, **_kwargs: object) -> dict[str, Any]:
        calls.append(args)
        return {"ok": True, "stdout": "Model: deepseek-v4-pro\nProvider: Ollama Cloud", "stderr": ""}

    monkeypatch.setattr(builtins, "_run_hermes_desktop", fake_desktop)
    info = await HermesAgentAdapter().health_probe()

    assert calls == [["status"]]
    assert info.health_state == HealthState.HEALTHY
```

- [ ] **Step 2: Run the test and verify it fails because the adapter uses `_run_bounded("hermes", ...)` and requires `Custom endpoint`.**

Run: `python -m pytest tests/test_agent_adapters.py::test_hermes_health_uses_desktop_runtime_and_configured_provider -q`

Expected: FAIL.

- [ ] **Step 3: Add the direct Desktop runner and change the health predicate.**

```python
HERMES_DESKTOP_HOME = Path(os.getenv("HERMES_DESKTOP_HOME", Path.home() / "AppData" / "Local" / "hermes"))
HERMES_DESKTOP_EXE = HERMES_DESKTOP_HOME / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"

async def _run_hermes_desktop(args: list[str], timeout: float = 30, *, cwd: Path | None = None) -> dict[str, Any]:
    if not HERMES_DESKTOP_EXE.is_file():
        return {"ok": False, "error": f"Desktop Hermes executable missing: {HERMES_DESKTOP_EXE}"}
    return await _run_executable_bounded(HERMES_DESKTOP_EXE, args, timeout, env={"HERMES_HOME": str(HERMES_DESKTOP_HOME)}, cwd=cwd)
```

Set `HEALTHY` only when `status` succeeds and both non-empty `Model:` and `Provider:` fields are present. Preserve Nous Portal state as a separate capability/auth signal; it must not downgrade a working configured Desktop model.

- [ ] **Step 4: Run the adapter tests.**

Run: `python -m pytest tests/test_agent_adapters.py -q`

Expected: PASS.

### Task 2: Persist source-backed unfinished-work candidates

**Files:**

- Modify: `orchestrator/state/schema.sql`
- Modify: `orchestrator/state/store.py`
- Create: `orchestrator/backlog/__init__.py`
- Create: `orchestrator/backlog/discovery.py`
- Test: `tests/test_backlog_discovery.py`

**Interfaces:**

- Produces: `discover_backlog_candidates(roots: list[Path]) -> list[dict[str, Any]]`
- Produces: `StateStore.upsert_discovered_work_item(candidate) -> dict[str, Any]`
- Produces: `StateStore.list_discovered_work_items(states=None, limit=...) -> list[dict[str, Any]]`

- [ ] **Step 1: Write failing tests for ready ledger tasks, malformed ledgers, and idempotent source fingerprints.**

```python
def test_discovery_extracts_ready_task_with_source_evidence(tmp_path: Path) -> None:
    ledger = tmp_path / "TASKS.yaml"
    ledger.write_text("tasks:\n  - id: TASK-1\n    title: Verify runtime\n    status: ready\n    description: Run pytest.\n", encoding="utf-8")

    candidates = discover_backlog_candidates([tmp_path])

    assert candidates[0]["source_path"] == str(ledger)
    assert candidates[0]["source_task_id"] == "TASK-1"
    assert candidates[0]["status"] == "ready"

def test_discovery_preserves_malformed_ledger_as_repair_candidate(tmp_path: Path) -> None:
    ledger = tmp_path / "TASKS.yaml"
    ledger.write_text("tasks:\n  - id: TASK-1\n    title: broken\n  TASK-2:\n", encoding="utf-8")

    candidate = discover_backlog_candidates([tmp_path])[0]

    assert candidate["kind"] == "malformed_task_ledger"
    assert candidate["source_line"] is not None
```

- [ ] **Step 2: Run the tests and verify they fail because the discovery module and table do not exist.**

Run: `python -m pytest tests/test_backlog_discovery.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Add the durable candidate table and bounded parser.**

```sql
CREATE TABLE IF NOT EXISTS discovered_work_items (
  id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
  source_path TEXT NOT NULL, source_line INTEGER, source_task_id TEXT,
  project_root TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
  source_status TEXT NOT NULL, priority TEXT, state TEXT NOT NULL,
  delegated_work_id TEXT, error TEXT, payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
```

Parse only `TASKS.yaml`, `TASKS.yml`, `TODO.md`, `BACKLOG.md`, and `PLAN.md` files discovered at a bounded depth. Promote `ready`, `active`, and `blocked` task-ledger entries; ignore terminal statuses such as `done`, `complete`, and `completed`. A YAML parser failure yields one `malformed_task_ledger` candidate with `state="discovered"`, line number, and parser message.

- [ ] **Step 4: Run the discovery tests.**

Run: `python -m pytest tests/test_backlog_discovery.py -q`

Expected: PASS.

### Task 3: Promote real candidates into the existing work cycle

**Files:**

- Modify: `orchestrator/delegation/work_cycle.py`
- Modify: `orchestrator/scheduler/tasks.py`
- Modify: `orchestrator/process/supervisor.py`
- Modify: `orchestrator/cli/delegation.py`
- Test: `tests/test_backlog_discovery.py`

**Interfaces:**

- Produces: `intake_discovered_work(settings, store, roots, limit=3) -> dict[str, Any]`
- Adds scheduler type: `backlog_discovery`
- Adds CLI command: `work-discover [--limit N]`

- [ ] **Step 1: Write failing promotion and scheduler tests.**

```python
@pytest.mark.asyncio
async def test_intake_promotes_ready_candidate_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    (tmp_path / "TASKS.yaml").write_text("tasks:\n  - id: TASK-1\n    title: Verify runtime\n    status: ready\n    description: Run pytest.\n", encoding="utf-8")
    store = StateStore(settings)
    await store.initialize()

    result = await intake_discovered_work(settings, store, roots=[tmp_path], limit=3)
    items = await store.list_delegated_work_items()

    assert result["promoted"] == 1
    assert items[0]["source"] == "backlog_discovery"
    assert "TASK-1" in items[0]["raw_text"]
```

- [ ] **Step 2: Run the test and verify it fails because no intake function or scheduler handler exists.**

Run: `python -m pytest tests/test_backlog_discovery.py::test_intake_promotes_ready_candidate_once -q`

Expected: FAIL.

- [ ] **Step 3: Implement intake and register it before the delegated-work cycle.**

```python
candidate = await store.upsert_discovered_work_item(source_candidate)
if candidate["state"] == "discovered" and promoted < limit:
    work = await store.enqueue_delegated_work_item({
        "source": "backlog_discovery",
        "raw_text": source_backed_instruction(candidate),
        "consumer": f"backlog:{candidate['project_root']}",
        "job_class": "repo_coding",
        "project_root": candidate["project_root"],
        "mode": "immediate",
        "operation_task": source_backed_operation_task(candidate),
        "min_quality_score": 1.0,
    })
    await store.mark_discovered_work_promoted(candidate["id"], work["id"])
```

The source-backed instruction must name the exact source file, task ID, title, description, project root, and non-external action boundary. The new scheduler runs discovery every 15 minutes and the existing five-minute work cycle consumes the resulting queue.

- [ ] **Step 4: Run discovery, scheduler, and delegated-work tests.**

Run: `python -m pytest tests/test_backlog_discovery.py tests/test_delegated_work.py tests/test_crash_audit_scheduler.py tests/test_supervisor.py -q`

Expected: PASS.

### Task 4: Prove real intake and Desktop Hermes against source work

**Files:**

- Runtime evidence: `.runtime/orchestrator/state.sqlite`
- Runtime evidence: `.runtime/orchestrator/ORCHESTRATOR_OUTPUT/.../receipt.json`

- [ ] **Step 1: Run Desktop Hermes no-mutation smoke through its Desktop home.**

Run: `HERMES_HOME=C:\Users\Couch\AppData\Local\hermes C:\Users\Couch\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe -z "Return HERMES_OK" --usage-file <runtime receipt path>`

Expected: `HERMES_OK` plus a usage JSON artifact.

- [ ] **Step 2: Run `work-discover` against the default mapped roots.**

Expected: ready shared-drive task ledgers and malformed task ledgers become durable discovered items; terminal tasks do not enter the queue.

- [ ] **Step 3: Run one source-backed, non-external validation task through the scheduler.**

Expected: source reference, project root, result, receipt, worker identity, and validator result all persist in the work item.

- [ ] **Step 4: Verify the running operator surface and focused tests.**

Run:

```powershell
python -m pytest -q tests/test_agent_adapters.py tests/test_backlog_discovery.py tests/test_delegated_work.py tests/test_crash_audit_scheduler.py tests/test_supervisor.py
python -m orchestrator.cli.main work-status --last 20
Invoke-WebRequest http://127.0.0.1:8765/api/dashboard-status -TimeoutSec 5
```

Expected: the dashboard reports the Desktop Hermes configured route, the queue contains source-backed work rather than only manually submitted proof prompts, and every promoted runtime item retains source evidence.

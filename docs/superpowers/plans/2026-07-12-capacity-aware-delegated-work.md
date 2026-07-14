# Capacity-Aware Delegated Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn an owner-approved work item into a capacity-governed specialist dispatch with a verified output, receipt, quality result, and durable lifecycle record.

**Architecture:** Add a `delegated_work_items` table and store API, then implement one work-cycle service that selects a queued item, holds discretionary work unless a measured quota window is about to expire, invokes the existing `Dispatcher`, and accepts only outputs meeting the item’s validator threshold. Expose submit/status/cycle CLI commands and a scheduler task type so the running orchestrator can continue the queue without resurrecting legacy automations.

**Tech Stack:** Python 3.13, aiosqlite, existing `Dispatcher`, worker-aware router, subscription-usage snapshots, scheduler task registry, pytest.

## Global Constraints

- Never invoke an unknown-cost or external-action route; the existing dispatcher and project policy remain the authority.
- Immediate work uses normal route reserve protection. Discretionary work requires a measured provider window with `remaining_percent >= min_surplus_percent` and a reset inside `surplus_window_seconds`.
- No model self-report is accepted as usage evidence; quota state comes from `subscription_usage_snapshots` and dispatched token receipts.
- A work item reaches `completed` only after a real dispatch has an output path and its deterministic validator score meets `min_quality_score`.
- Legacy scheduler tasks remain disabled. The new scheduler task is a new source-first capability with a named consumer and receipt.

---

### Task 1: Persist delegated work items

**Files:**

- Modify: `orchestrator/state/schema.sql`
- Modify: `orchestrator/state/store.py`
- Test: `tests/test_delegated_work.py`

**Interfaces:**

- Produces: `StateStore.enqueue_delegated_work_item(payload) -> dict[str, Any]`
- Produces: `StateStore.list_delegated_work_items(states=None, limit=100) -> list[dict[str, Any]]`
- Produces: `StateStore.update_delegated_work_item(item_id, state, **fields) -> dict[str, Any] | None`

- [ ] **Step 1: Write the failing persistence tests**

```python
@pytest.mark.asyncio
async def test_delegated_work_item_round_trips_with_owner_and_consumer(tmp_path: Path) -> None:
    store = StateStore(_settings(tmp_path))
    await store.initialize()
    item = await store.enqueue_delegated_work_item({
        "raw_text": "Return a JSON proof.", "consumer": "runtime-proof",
        "job_class": "routing_triage", "mode": "immediate",
        "operation_task": {"task_id": "proof", "validator": "required_fields_and_terms", "expected": {}},
    })
    rows = await store.list_delegated_work_items(states={"queued"})
    assert rows[0]["id"] == item["id"]
    assert rows[0]["consumer"] == "runtime-proof"
```

- [ ] **Step 2: Run the focused test and verify it fails because the store API is absent**

Run: `python -m pytest tests/test_delegated_work.py::test_delegated_work_item_round_trips_with_owner_and_consumer -q`

Expected: FAIL with `AttributeError` for `enqueue_delegated_work_item`.

- [ ] **Step 3: Add the table and minimal store methods**

```sql
CREATE TABLE IF NOT EXISTS delegated_work_items (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, raw_text TEXT NOT NULL,
  consumer TEXT NOT NULL, job_class TEXT NOT NULL, mode TEXT NOT NULL,
  operation_task_json TEXT, min_quality_score REAL NOT NULL,
  state TEXT NOT NULL, dispatch_id TEXT, output_path TEXT, receipt_path TEXT,
  validation_json TEXT, error TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, completed_at TEXT
);
```

Use UUID IDs with a `wrk_` prefix, JSON encode/decode `operation_task_json` and `validation_json`, and write an audit row for enqueue and lifecycle updates.

- [ ] **Step 4: Run the focused persistence tests**

Run: `python -m pytest tests/test_delegated_work.py -q`

Expected: PASS.

### Task 2: Make the existing dispatcher carry a work-item validator

**Files:**

- Modify: `orchestrator/dispatch/dispatcher.py`
- Test: `tests/test_quality_loop.py`
- Test: `tests/test_delegated_work.py`

**Interfaces:**

- Changes: `Dispatcher.dispatch_text(raw_text, project_root=None, job_class_override=None, source="user", operation_task=None)`
- Produces: a normal `DispatchResult` whose receipt contains `operation_quality_score` when `operation_task` is supplied.

- [ ] **Step 1: Write the failing forwarding test**

```python
@pytest.mark.asyncio
async def test_dispatch_text_persists_supplied_operation_task(tmp_path: Path) -> None:
    dispatcher, store = await _seed_dispatcher(tmp_path)
    result = await dispatcher.dispatch_text(
        "Return JSON.", job_class_override="routing_triage", source="delegated_work",
        operation_task=_proof_operation_task(),
    )
    receipt = (await store.get_receipt(result.dispatch_id))
    assert receipt["operation_quality_score"]["composite_score"] == 1.0
```

- [ ] **Step 2: Run the test and verify it fails because `dispatch_text` rejects the new arguments**

Run: `python -m pytest tests/test_delegated_work.py::test_dispatch_text_persists_supplied_operation_task -q`

Expected: FAIL with an unexpected keyword argument error.

- [ ] **Step 3: Add the minimal forwarding code**

```python
intent = parse_intent(raw_text)
intent.source = source
if operation_task is not None:
    intent.parsed_payload["operation_task"] = operation_task
```

Keep the existing routing, adapter, receipt, and quality code unchanged.

- [ ] **Step 4: Run the dispatcher and quality tests**

Run: `python -m pytest tests/test_quality_loop.py tests/test_delegated_work.py -q`

Expected: PASS.

### Task 3: Run capacity-aware queued work

**Files:**

- Create: `orchestrator/delegation/work_cycle.py`
- Modify: `orchestrator/scheduler/tasks.py`
- Test: `tests/test_delegated_work.py`

**Interfaces:**

- Produces: `run_delegated_work_cycle(settings, store, limit=1) -> dict[str, Any]`
- Consumes: queued work items, subscription usage snapshots, `Dispatcher`.
- Produces: `completed`, `held`, `failed`, or `awaiting_approval` lifecycle states with dispatch/validation references.

- [ ] **Step 1: Write the failing cycle tests**

```python
@pytest.mark.asyncio
async def test_immediate_work_dispatches_and_completes_after_full_validation(tmp_path: Path, monkeypatch) -> None:
    store = await _store_with_queued_proof(tmp_path, mode="immediate")
    monkeypatch.setattr(work_cycle, "Dispatcher", FakeDispatcher)
    result = await work_cycle.run_delegated_work_cycle(store.settings, store)
    assert result["completed"] == 1
    assert (await store.list_delegated_work_items())[0]["state"] == "completed"

@pytest.mark.asyncio
async def test_discretionary_work_holds_without_expiring_measured_surplus(tmp_path: Path) -> None:
    store = await _store_with_queued_proof(tmp_path, mode="discretionary")
    result = await work_cycle.run_delegated_work_cycle(store.settings, store)
    assert result["held"] == 1
    assert (await store.list_delegated_work_items())[0]["state"] == "held"
```

- [ ] **Step 2: Run the cycle tests and verify they fail because the module is absent**

Run: `python -m pytest tests/test_delegated_work.py -q`

Expected: FAIL with `ModuleNotFoundError: orchestrator.delegation`.

- [ ] **Step 3: Implement one-item-at-a-time execution**

```python
if item["mode"] == "discretionary" and not has_expiring_surplus(snapshots, now):
    await store.update_delegated_work_item(item["id"], "held", error="no_expiring_measured_surplus")
    return held_result
result = await dispatcher.dispatch_text(
    item["raw_text"], project_root=project_root, job_class_override=item["job_class"],
    source="delegated_work", operation_task=item["operation_task"],
)
validation = result.receipt.get("operation_quality_score", {}) if result.receipt else {}
state = "completed" if result.state == "completed" and validation.get("composite_score", 0.0) >= item["min_quality_score"] else "failed"
```

Add scheduler task type `delegated_work_cycle`, record `mark_scheduler_task_run`, and never auto-enable a legacy task.

- [ ] **Step 4: Run the focused cycle and scheduler tests**

Run: `python -m pytest tests/test_delegated_work.py tests/test_crash_audit_scheduler.py -q`

Expected: PASS.

### Task 4: Expose the owner-facing runtime controls and prove the live route

**Files:**

- Modify: `orchestrator/cli/main.py`
- Create: `orchestrator/cli/delegation.py`
- Modify: `tests/test_delegated_work.py`

**Interfaces:**

- `orchestrator work-submit --text ... --consumer ... --job-class ... --operation-task-json ...`
- `orchestrator work-cycle --limit 1`
- `orchestrator work-status --last 20`

- [ ] **Step 1: Write the failing CLI registration test**

```python
def test_cli_exposes_delegated_work_commands() -> None:
    output = subprocess.run([PYTHON, "-m", "orchestrator.cli.main", "--help"], capture_output=True, text=True, check=True).stdout
    assert "work-submit" in output
    assert "work-cycle" in output
    assert "work-status" in output
```

- [ ] **Step 2: Run the test and verify it fails because the commands are absent**

Run: `python -m pytest tests/test_delegated_work.py::test_cli_exposes_delegated_work_commands -q`

Expected: FAIL with an assertion that `work-submit` is missing.

- [ ] **Step 3: Register the commands and add the one new scheduler task**

```python
await store.upsert_scheduler_task(
    task_id="task_delegated_work_cycle", task_type="delegated_work_cycle",
    enabled=True, interval_seconds=300,
    payload={"consumer": "delegated_work_queue", "proof_kind": "live"},
)
```

The CLI must require a named consumer, accept only `immediate` or `discretionary` modes, and default `min_quality_score` to `1.0`.

- [ ] **Step 4: Run focused tests, full suite, and a live low-consequence proof work item**

Run:

```powershell
python -m pytest tests/test_delegated_work.py tests/test_quality_loop.py tests/test_crash_audit_scheduler.py -q
python -m pytest -q
python -m orchestrator.cli.main work-submit --text '<JSON-only proof prompt>' --consumer runtime-verification --job-class routing_triage --mode immediate --operation-task-json '<required_fields_and_terms task>'
python -m orchestrator.cli.main work-cycle --limit 1
python -m orchestrator.cli.main work-status --last 1
```

Expected: the final work item is `completed`, has a real local-worker dispatch ID, result path, receipt path, and validator composite score `1.0`.

### Task 5: Check the running scheduler consumes the queue without a manual command

**Files:**

- Modify: `tests/test_delegated_work.py`
- Runtime receipt: `.runtime/orchestrator/ORCHESTRATOR_OUTPUT/.../receipt.json`

- [ ] **Step 1: Submit a second low-consequence proof item with the same validator**

- [ ] **Step 2: Wait for the registered five-minute task only by polling its scheduler and work-item state**

Run: `python -m orchestrator.cli.main work-status --last 2`

Expected: the second item becomes `completed` without invoking `work-cycle` manually.

- [ ] **Step 3: Run the final runtime proof checks**

```powershell
python -m orchestrator.cli.main spec-status
python -m orchestrator.cli.main work-status --last 2
```

Expected: live status is readable; both proof items have output paths, receipts, real dispatch IDs, and full validator scores.

## Execution Record — 2026-07-12

- [x] Work items now have a durable owner/consumer lifecycle and audit trail.
- [x] The normal dispatcher carries each item validator through routing, execution, receipt, and quality scoring.
- [x] The five-minute scheduler consumed a queued item without a manual `work-cycle` invocation: `wrk_cab5c2ae46384fa3` reached `completed` through `dsp_4440fa6d89d94883`, with persisted output, live receipt, and a `1.0` composite score.
- [x] The local Ollama adapter now targets the current localhost server rather than the stale IPv4 WSL relay, and its health probe verifies the dispatch route.
- [x] The operator dashboard now bounds supervisor-receipt parsing; its live endpoint returned in about two seconds after restart despite 8,461 historical supervisor receipts.
- [x] Focused verification: 46 tests passed. `spec-status` reports 24 passed, 0 partial, and 0 missing targets.
- [!] The broad legacy test suite exceeded its 180-second ceiling without emitting a test failure. It remains a separate test-suite performance repair, not evidence against the runtime receipts above.

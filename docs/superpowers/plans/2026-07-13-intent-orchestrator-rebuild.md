# Intent Orchestrator Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the existing AI Orchestrator into the owner-controlled three-pane interaction layer defined by the 53 locked decisions, then prove it by using the slice itself to build transcript intelligence, Hermes/Gemini service expansion, and Gmail-to-Hermes-WhatsApp decision escalation.

**Architecture:** Extend the current FastAPI, vanilla JavaScript, Python 3.13, and SQLite system. Add an append-only workbench event ledger with rebuildable projections, a shared-frame and decision service, managed service adapters, transcript-source adapters, notification-channel adapters, and an owner-facing workbench. Bootstrap only the core plus Claude/Codex; every later capability must be built through that live slice.

**Tech Stack:** Python 3.13, Pydantic, aiosqlite/SQLite 3.49.1 with WAL and FTS5, FastAPI/Starlette SSE, httpx, watchdog, vanilla HTML/CSS/JavaScript, pytest, and Playwright for the real browser path.

## Global Constraints

- The 53 decisions in `docs/pending/intent_orchestrator_rebuild_decision_ledger_2026-07-13.md` are the product contract.
- Preserve the dirty worktree. Re-read every target file and its diff immediately before editing; never reset, stash, overwrite, or commit unrelated owner changes.
- The existing state database remains authoritative. New tables arrive through atomic, checksum-recorded migrations and are replayable from the event ledger.
- No regex, prompt hook, Stop hook, or possibility-count system may decide whether owner intent was understood.
- No hidden chain-of-thought is requested, stored, or represented. Store positions, assumptions, evidence, alternatives, questions, actions, and receipts.
- No visible Claude, Codex, browser, terminal, Phone Link, or WhatsApp UI automation is part of the runtime.
- A missing connector or service is a visible repair state. It may not be replaced by a weaker authority surface without owner approval.
- No external or irreversible action runs without the task's recorded authority and an idempotency key.
- Completion is impossible while a required success condition lacks an authority-surface readback.
- Errors cannot be made impossible; they must be detected, contained to dependent work, preserved across restart, repaired through a changed mechanism, and never reported as success.
- The old bounded-outcome tables and evidence remain intact and receive no new live writes after cutover.
- No new scheduled loop is enabled until its named consumer, maximum cadence, resource ceiling, receipt, and benefit test pass.

---

## Public Interfaces and State Contract

Create `orchestrator/workbench/models.py` with these public enums and Pydantic models:

```python
class TaskPurpose(StrEnum):
    ANSWER = "answer"
    RESEARCH = "research"
    EXPLORE = "explore"
    DESIGN = "design"
    EXECUTE = "execute"
    MONITOR = "monitor"
    REVIEW = "review"

class AuthorityMode(StrEnum):
    ASK_FIRST = "ask_first"
    RECOMMEND_THEN_ASK = "recommend_then_ask"
    DECIDE_REVERSIBLE = "decide_reversible"
    DECIDE_WITHIN_BOUNDARIES = "decide_within_boundaries"

class TaskState(StrEnum):
    INTAKE = "intake"
    FRAME_PENDING = "frame_pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_OWNER = "waiting_owner"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    REPAIRING = "repairing"

class DecisionState(StrEnum):
    QUEUED = "queued"
    ACTIVE = "active"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"

class WorkbenchEvent(BaseModel):
    event_id: str
    task_id: str
    sequence: int
    event_type: str
    actor_kind: str
    actor_id: str
    branch_id: str = "main"
    caused_by: str | None = None
    frame_version: int
    payload: dict[str, Any]
    idempotency_key: str
    created_at: datetime
    checksum: str

class FrameNode(BaseModel):
    node_id: str
    kind: Literal["goal", "success", "constraint", "assumption", "alternative", "authority", "evidence_gap", "decision", "action"]
    text: str
    status: Literal["proposed", "confirmed", "rejected", "invalidated"]
    depends_on: list[str] = Field(default_factory=list)
    provenance_event_ids: list[str] = Field(default_factory=list)

class DecisionRequest(BaseModel):
    decision_id: str
    task_id: str
    question: str
    options: list[dict[str, str]]
    free_form_allowed: bool = True
    recommendation: str | None = None
    consequence_if_unresolved: str
    affected_node_ids: list[str]
    tier: Literal["routine", "blocking", "critical"]
    state: DecisionState

class ServiceRun(BaseModel):
    run_id: str
    task_id: str
    service: Literal["claude", "codex", "hermes", "gemini", "local"]
    role: str
    frame_version: int
    branch_id: str
    state: Literal["queued", "running", "waiting_owner", "paused_dependency", "repairing", "verifying", "complete", "failed"]
    native_session_id: str | None = None
    receipt_event_id: str | None = None
```

Create `orchestrator/adapters/session.py` with the managed adapter contract:

```python
class EventSink(Protocol):
    async def emit(self, event_type: str, payload: dict[str, Any], *, idempotency_key: str) -> WorkbenchEvent: ...

class ManagedSessionAdapter(Protocol):
    name: str
    async def start(self, envelope: RunEnvelope, sink: EventSink) -> ServiceRunHandle: ...
    async def answer(self, handle: ServiceRunHandle, decision: DecisionResolution) -> None: ...
    async def pause(self, handle: ServiceRunHandle, reason: str) -> None: ...
    async def resume(self, handle: ServiceRunHandle, frame: SharedFrame) -> None: ...
    async def cancel(self, handle: ServiceRunHandle, reason: str) -> None: ...
    async def export_transcript(self, handle: ServiceRunHandle) -> TranscriptExport: ...
```

Create `orchestrator/transcripts/base.py` with `TranscriptSource.scan(cursor) -> SourceBatch` and `TranscriptSource.read(document_ref) -> TranscriptDocument`. Create `orchestrator/notifications/channels.py` with `DecisionChannel.deliver(notification) -> DeliveryReceipt` and `DecisionChannel.poll(cursor) -> ReplyBatch`.

The public API is:

- `POST /api/workbench/tasks`
- `GET /api/workbench/tasks/{task_id}`
- `GET /api/workbench/tasks/{task_id}/events?after={sequence}` as SSE
- `POST /api/workbench/tasks/{task_id}/attachments`
- `POST /api/workbench/tasks/{task_id}/transcribe`
- `POST /api/workbench/tasks/{task_id}/frame/proposals`
- `POST /api/workbench/tasks/{task_id}/frame/proposals/{proposal_id}/confirm`
- `POST /api/workbench/tasks/{task_id}/frame/proposals/{proposal_id}/reject`
- `POST /api/workbench/tasks/{task_id}/decisions/{decision_id}/resolve`
- `POST /api/workbench/tasks/{task_id}/decisions/reorder`
- `POST /api/workbench/tasks/{task_id}/runs/start`
- `POST /api/workbench/tasks/{task_id}/runs/{run_id}/pause|resume|cancel`
- `GET /api/workbench/transcripts/search`
- `POST /api/workbench/transcripts/{document_id}/exclude|include|forget`
- `GET /api/workbench/notifications/status`

## User Interaction Contract

1. **New task:** one request enters adaptive intake. Known context is prefilled; one consequential question is active at a time.
2. **Simple request:** the inferred frame appears immediately and execution starts without a forced confirmation unless the owner edits or stops it.
3. **Ambiguous or consequential request:** Conversation explains the current interpretation; Shared Frame shows goals, alternatives, assumptions, authority, evidence gaps, and success; Now shows the active decision.
4. **Multiple choice:** recommendation is visible and explained, never selected. Free-form remains equally available.
5. **Owner edits:** an edit becomes a proposal. The workbench shows affected frame nodes, branches, runs, and artifacts before confirmation.
6. **Correction during work:** only dependent runs pause. Unaffected research and work continue.
7. **Model disagreement:** distinct positions and evidence remain visible until evidence resolves the difference or the owner decides.
8. **Evidence checkpoint:** research displays source coverage, contrary evidence, unresolved gaps, and consequence. The owner may continue research or authorize design/execution.
9. **Service selection:** the workbench recommends a service team and reason; the owner may add, remove, replace, or lock participants at any time.
10. **Direct vendor session:** it is labeled ungoverned. Its transcript is auto-indexed and may be reconciled into a chosen task/frame.
11. **Transcript control:** Exclude immediately removes a conversation from retrieval and learning but is reversible. Forget requires confirmation, removes copied content, FTS rows, embeddings, summaries, and caches, and leaves a tombstone without message content.
12. **Blocking owner decision:** send Gmail immediately. If unresolved for one hour, send one Hermes WhatsApp escalation. A valid reply from either channel resolves the same decision and closes duplicates.
13. **Unattended task:** continue only reversible branches inside standing authority. External or irreversible branches wait.
14. **Completion:** Review compares every success node with real readback. Missing proof returns the task to Repairing; it cannot display Complete.
15. **Responsive layout:** desktop defaults to 30/46/24 with draggable borders. Narrow screens use Conversation, Frame, and Now tabs without feature loss.
16. **Files and images:** drag, paste, or select files/screenshots. The attachment appears in Conversation and as a source-scoped frame input before any model receives it.
17. **Voice:** microphone input shows interim and final transcription in an editable box. Nothing enters the frame until the owner accepts or edits the transcript.

## Service and Surface Coverage

- **Claude Code CLI:** managed streaming-JSON session with exact session ID, native questions, pause/resume, tool events, transcript export, and receipts.
- **Claude Desktop/Cowork:** a local MCP bridge exposes `workbench_attach`, `workbench_get_frame`, `workbench_ask_owner`, `workbench_emit_evidence`, and `workbench_close_run`. A conversation is governed only after a successful attach event. Other chats remain usable, visibly ungoverned, and auto-indexed when locally accessible or exported.
- **Codex Desktop:** use the local app-server protocol so created threads, turns, native user-input requests, plugins/connectors, and tool results remain visible to the workbench.
- **Codex CLI:** governed launches use app-server or managed JSONL execution. Direct CLI sessions remain usable and are reconciled from `.codex/sessions`.
- **Hermes CLI/Desktop:** governed roles use ACP; direct sessions use Hermes' session export/index route. Messaging uses a separate owner-only gateway profile.
- **Gemini CLI:** governed roles use ACP and native session IDs; direct sessions import from `.gemini/tmp/*/chats`.
- **Local and other adapters:** the existing one-shot adapter contract remains available for simple work. A service without frame injection, questions, pause/resume, telemetry, transcript export, or receipts displays those missing capabilities before selection.

---

### Task 1: Preserve Current State and Establish Atomic Migrations

**Files:**
- Create: `orchestrator/state/migrations.py`
- Create: `orchestrator/state/migrations/0002_workbench_core.sql`
- Modify: `orchestrator/state/store.py`
- Test: `tests/test_workbench_migrations.py`

**Interfaces:** `MigrationRunner.apply(db) -> list[int]`; migration 2 creates `workbench_events`, `workbench_tasks`, `frame_nodes`, `frame_edges`, `frame_proposals`, `decision_requests`, `service_runs`, `evidence_refs`, `learning_proposals`, `quarantined_components`, and projection metadata.

- [ ] Write a migration test that initializes a fresh database and a copy of the current schema, verifies migration 2 applies once, verifies a second initialize is a no-op, and verifies rollback leaves the prior schema readable after an injected failure.
- [ ] Add `checksum`, `name`, and `status` columns to `schema_migrations` through guarded column checks; record the SHA-256 of each migration file.
- [ ] Implement `MigrationRunner` with `BEGIN IMMEDIATE`, SQLite online backup to `settings.home / "migration-backups"`, checksum comparison, rollback, and a repair-queue item on failure.
- [ ] Run `pytest tests/test_workbench_migrations.py -v`; expected result: all migration, idempotency, backup, rollback, and checksum tests pass.

### Task 2: Implement the Append-Only Event Ledger and Projections

**Files:**
- Create: `orchestrator/workbench/models.py`
- Create: `orchestrator/workbench/events.py`
- Create: `orchestrator/workbench/projector.py`
- Create: `orchestrator/workbench/store.py`
- Test: `tests/test_workbench_events.py`

**Interfaces:** `WorkbenchStore.append(task_id, event_type, payload, actor, idempotency_key) -> WorkbenchEvent`; `WorkbenchStore.snapshot(task_id) -> WorkbenchSnapshot`; `Projector.rebuild(task_id) -> WorkbenchSnapshot`.

- [ ] Test two concurrent appends, duplicate idempotency keys, checksum tampering, crash-before-commit, and full projection rebuild.
- [ ] Allocate `sequence` under `BEGIN IMMEDIATE`; hash canonical JSON containing task, sequence, type, actor, frame version, payload, and prior checksum.
- [ ] Apply event and projection changes in one transaction. Treat projections as disposable and verify rebuild equality with canonical JSON.
- [ ] Run `pytest tests/test_workbench_events.py -v`; expected result: contiguous sequences, one row per idempotency key, tampering detected, and replay equals live projection.

### Task 3: Build Shared Frame, Dependency Graph, Decisions, and Corrections

**Files:**
- Create: `orchestrator/workbench/frame.py`
- Create: `orchestrator/workbench/decisions.py`
- Create: `orchestrator/workbench/materiality.py`
- Test: `tests/test_shared_frame.py`
- Test: `tests/test_decision_queue.py`

**Interfaces:** `FrameService.propose_change`; `FrameService.confirm_change`; `DecisionService.enqueue_native_question`; `DecisionService.resolve`; `MaterialityGate.evaluate(candidate) -> ASK|DISPLAY_DEFAULT|IGNORE`.

- [ ] Test goal/success/constraint/assumption/alternative nodes, cycle rejection, proposal impact previews, branch forks, time travel, and correction invalidation.
- [ ] Require every question candidate to name a concrete changed outcome in intent, consequence, authority, cost, UX, or external action. The gate evaluates declared effects; it does not scan human language with patterns.
- [ ] Normalize native questions by semantic identity and affected nodes; merge duplicates without discarding service provenance.
- [ ] On correction, traverse `frame_edges`, emit `frame.node.invalidated`, and pause only `service_runs` whose recorded inputs include affected node IDs.
- [ ] Run both test files; expected result: no unrelated branch pauses and no nonmaterial question reaches the active queue.

### Task 4: Implement Adaptive Guided Intake and Service-Team Recommendation

**Files:**
- Create: `orchestrator/workbench/intake.py`
- Create: `orchestrator/workbench/recommendation.py`
- Modify: `orchestrator/routing/brain.py`
- Test: `tests/test_workbench_intake.py`

**Interfaces:** `IntakeEngine.begin(TaskCreateRequest) -> IntakeResult`; `IntakeEngine.apply_resolution`; `TeamRecommender.recommend(frame, capability_inventory) -> ServiceRecommendation`.

- [ ] Use structured Claude/Codex interpretation candidates for consequential tasks: interpretation, alternatives, assumptions, evidence gaps, material decisions, and success conditions. Preserve both candidates in the ledger.
- [ ] Auto-start a low-consequence complete task after emitting `frame.inferred`; do not insert a timer or fake confirmation.
- [ ] Stop intake when every missing field is either confirmed, explicitly delegated, or proven nonmaterial.
- [ ] Recommend the smallest competent team after tiered discovery across adapters, skills, callable connectors, local sources, and public research routes. Show unavailable mechanisms and repair state.
- [ ] Test free-form answers, recommendations without preselection, owner service overrides, and a model disagreement that remains visible.

### Task 5: Add Managed Claude and Codex Sessions

**Files:**
- Create: `orchestrator/adapters/claude_session.py`
- Create: `orchestrator/adapters/codex_app_server.py`
- Create: `orchestrator/adapters/session.py`
- Create: `orchestrator/mcp/workbench_server.py`
- Modify: `orchestrator/adapters/builtins.py`
- Test: `tests/test_managed_claude_session.py`
- Test: `tests/test_codex_app_server.py`

**Interfaces:** both implement `ManagedSessionAdapter`; existing `dispatch()` remains unchanged for one-shot routes.

- [ ] Claude starts with `--print --input-format stream-json --output-format stream-json --include-hook-events --replay-user-messages --session-id <uuid>` and a task-scoped permission mode. Parse assistant text, tool calls, hook events, session ID, usage, errors, and `AskUserQuestion` into workbench events.
- [ ] Codex starts through `codex app-server` JSON-RPC using `thread/start` and `turn/start`; answer server request `item/tool/requestUserInput` only after its normalized central decision resolves.
- [ ] Persist native session IDs before consuming output so restart recovery can resume rather than cross-bind to the latest session.
- [ ] Reject an event whose native session ID, task ID, run ID, or frame version does not match the handle.
- [ ] Implement the Claude Desktop/Cowork MCP bridge with task-scoped attach tokens. A tool call without a valid task, run, and current frame version is rejected and recorded; the bridge never edits Claude Desktop's active UI.
- [ ] Add MCP protocol tests for initialize, tools/list, attach, frame read, owner question, evidence emission, stale frame, and run close.
- [ ] Test partial JSON lines, duplicated events, service crash, timeout, late question, stale frame, restart/resume, and exact native question correlation.

### Task 6: Expose the Workbench API and Live Event Stream

**Files:**
- Create: `orchestrator/ui/workbench_api.py`
- Modify: `orchestrator/ui/server.py`
- Test: `tests/test_workbench_api.py`

**Interfaces:** the REST/SSE endpoints listed above; all mutation endpoints require `expected_frame_version` and `idempotency_key`.

- [ ] Register an `APIRouter(prefix="/api/workbench")`; keep existing routes unchanged.
- [ ] SSE sends events after the requested sequence, heartbeats without database writes, and replays missed events after reconnect.
- [ ] Return HTTP 409 with the current snapshot for stale frame versions; return the original result for duplicate idempotency keys.
- [ ] Store attachments under `settings.home / "workbench" / task_id / "attachments"` with content hash, MIME type, original name, size, and source event. Reject executable content and files over the configured 100 MB local limit before writing.
- [ ] Implement transcription as browser speech recognition when supported, with a governed local transcription adapter as the fallback. Both return editable text plus engine/source metadata; neither confirms the transcript for the owner.
- [ ] Test create, intake question, frame proposal, resolution, correction, run control, SSE disconnect/reconnect, stale writes, and restart readback.

### Task 7: Build the Three-Pane Workbench

**Files:**
- Create: `orchestrator/ui/static/workbench.html`
- Create: `orchestrator/ui/static/workbench.css`
- Create: `orchestrator/ui/static/workbench.js`
- Modify: `orchestrator/ui/server.py`
- Test: `tests/browser/test_workbench_ui.py`

**Interfaces:** `/workbench` serves the page; browser state is derived from the snapshot plus SSE events.

- [ ] Render Conversation, Shared Frame, and Now at CSS grid columns `30fr 46fr 24fr`; enforce readable minimums and collapse to tabs below 900 CSS pixels.
- [ ] Implement pointer and keyboard border resizing, reset-to-default, full-pane expansion, internal section resizing, and persistence in local browser storage.
- [ ] Render frame nodes and dependencies, alternatives, assumptions, evidence gaps, service positions, provenance, active decision, queue reorder, and service team controls.
- [ ] Expose free-form input beside options; recommendation has visual explanation and no selected state.
- [ ] Add drag/drop, file picker, clipboard-image paste, microphone start/stop, interim transcript, correction, and explicit “Use transcript” controls.
- [ ] Bind loopback without a login prompt. Private-network launch uses a generated 256-bit capability URL and Windows Private-profile firewall scope; the browser stores the capability after the first signed link so there is no username/password ceremony. Revocation rotates the capability and invalidates open LAN sessions.
- [ ] Test 1920x1080, 1440x900, 1280x720, 768x1024, and 390x844; verify no horizontal text clipping, all controls keyboard reachable, and full feature parity in tabs.

### Task 8: Add Completion Verification, Recovery, and Old-System Quarantine

**Files:**
- Create: `orchestrator/workbench/verifier.py`
- Create: `orchestrator/workbench/recovery.py`
- Create: `orchestrator/governance/quarantine.py`
- Modify: `orchestrator/process/supervisor.py`
- Test: `tests/test_workbench_verifier.py`
- Test: `tests/test_workbench_recovery.py`

**Interfaces:** `CompletionVerifier.verify(task_id) -> VerificationResult`; `RecoveryService.recover_open_tasks()`; `QuarantineRegistry.assert_inactive(component_id)`.

- [ ] Completion requires a named consumer and one verified `EvidenceRef` for every required success node. Model text, local logs, and screenshots count only when that is the actual authority surface.
- [ ] On startup, resume open runs by exact native session ID, requeue pending projections, and leave unresolved decisions active.
- [ ] Register old bounded-contract hooks, scripts, tables, and proof locations as quarantined. Remove them from dispatch and hook selection without deleting evidence.
- [ ] Assert a governed run adds zero rows to `contract_runs`, `possibility_items`, `equivalence_classes`, `evidence_items`, `counterexamples`, and `contract_decisions`.
- [ ] Add an automation benefit audit that records trigger, consumer, cadence, last useful receipt, resource cost, and keep/quarantine decision. Stop and quarantine any newly discovered visible-UI AI ping loop; preserve the general hidden orchestrator task unless evidence proves it wasteful.

### Task 9: Bootstrap-Slice Acceptance Gate

**Files:**
- Create: `tests/acceptance/test_bootstrap_slice.py`
- Create: `scripts/prove_workbench_bootstrap.ps1`

- [ ] Run a real owner task through intake, frame, Claude/Codex team recommendation, one genuine native question if material, a correction, dependent-only pause, action, and authority readback.
- [ ] Restart the server mid-task and prove identical snapshot and decision correlation after replay.
- [ ] Capture the browser path, event IDs, native session IDs, receipts, and success-node readbacks in one machine-generated acceptance receipt.
- [ ] Do not enable the three next capability plans unless this gate passes.

---

### Task 10: Build Transcript Intelligence Through the Slice

**Files:**
- Create: `orchestrator/state/migrations/0003_transcript_intelligence.sql`
- Create: `orchestrator/transcripts/base.py`
- Create: `orchestrator/transcripts/sources.py`
- Create: `orchestrator/transcripts/indexer.py`
- Create: `orchestrator/transcripts/search.py`
- Test: `tests/test_transcript_sources.py`
- Test: `tests/test_transcript_index.py`

**Interfaces:** source adapters for Claude Code `.claude/projects/**/*.jsonl`, Codex `.codex/sessions/**/*.jsonl` and `archived_sessions`, Gemini `.gemini/tmp/*/chats/session-*.json`, Hermes `sessions export`/session store, and locally readable Claude Desktop/Cowork session artifacts. Cloud-only chats require a supported export and remain visibly unavailable until one exists.

- [ ] Create `transcript_sources`, `transcript_documents`, `transcript_messages`, `transcript_chunks`, `transcript_cursors`, `transcript_exclusions`, `transcript_forget_tombstones`, and FTS5 tables.
- [ ] Parse by source-native stable IDs and message timestamps. Never use “latest session” to bind a task.
- [ ] Incrementally read append-only JSONL by byte cursor; handle partial final lines, truncation, rotation, archive moves, duplicate copies, malformed records, locked files, empty Hermes state, and source disappearance.
- [ ] Use watchdog with debounce for changed roots and a five-minute reconciliation scheduler task. The task remains disabled until a measured idle tick stays below 1% CPU and 50 MB working-set growth and writes one benefit receipt only when deltas exist.
- [ ] Implement FTS5 search plus optional local Ollama embeddings. Indexing remains healthy when embeddings are unavailable, but semantic coverage is marked partial and opens a repair item; first-release acceptance requires a proven local embedding route.
- [ ] Exclude removes all retrieval and learning visibility transactionally. Include restores it. Forget deletes copied message bodies, chunks, FTS rows, embeddings, summaries, and caches, then retains only source ID hash, timestamp, and purge receipt.
- [ ] Add workbench search, source-health, coverage, Exclude, Include, and Forget UI.
- [ ] Through the live slice, have Claude and Codex build this task, correct one real dependency during work, and pass parser fixtures plus live readback from all accessible source roots.

### Task 11: Build Hermes and Gemini Managed Adapters Through the Slice

**Files:**
- Create: `orchestrator/adapters/acp_client.py`
- Create: `orchestrator/adapters/hermes_session.py`
- Create: `orchestrator/adapters/gemini_session.py`
- Modify: `orchestrator/adapters/builtins.py`
- Modify: `orchestrator/workbench/recommendation.py`
- Test: `tests/test_acp_client.py`
- Test: `tests/test_hermes_gemini_sessions.py`

**Interfaces:** `AcpClient` owns JSON-RPC framing, session lifecycle, notifications, tool approvals, and cancellation; both adapters implement `ManagedSessionAdapter`.

- [ ] Run `hermes acp --check` and a Gemini ACP initialization conformance probe before enabling either adapter.
- [ ] Map Hermes clarification and Gemini `ask_user`/ACP permission requests into the same central decision schema; answer only after a matching resolution event.
- [ ] Export exact session transcripts and ingest them through Task 10. Empty prior Hermes history is healthy, not a failure.
- [ ] Extend team recommendation with service capability, consequence ceiling, account/quota state, tool/skill inventory, and owner overrides. Never choose by majority vote.
- [ ] Test missing executable, broken ACP framing, provider auth drift, quota exhaustion, crash, late reply, cancellation, frame correction, and resume.
- [ ] Through the live slice, have Claude and Codex build both adapters, then run one four-service research/design task with preserved disagreement and independent receipts.

### Task 12: Build Gmail Decision Delivery and Reply Correlation Through the Slice

**Files:**
- Create: `orchestrator/state/migrations/0004_decision_notifications.sql`
- Create: `orchestrator/notifications/channels.py`
- Create: `orchestrator/notifications/codex_gmail_bridge.py`
- Create: `orchestrator/notifications/decision_email.py`
- Test: `tests/test_decision_email.py`
- Test: `tests/test_codex_gmail_bridge.py`

**Interfaces:** `CodexGmailBridge.get_profile`, `send_decision`, `search_replies`, and `read_thread`; each returns the real connector tool name, connector result ID, Gmail message/thread ID, and readback.

- [ ] Use Codex app-server `app/list` to prove the installed Gmail app exists, then run a read-only `_get_profile` call and require `owner@example.com` before enabling delivery.
- [ ] Execute Gmail calls through a narrow Codex app-server connector task. Accept success only when the event stream contains the intended Gmail tool call and a successful connector result; model prose is not a delivery receipt.
- [ ] Auto-send only to the configured owner allowlist, initially `owner@example.com`; all other recipients are rejected before dispatch.
- [ ] Create or reuse one Gmail thread per decision. Include `X-AI-Orchestrator-Decision`, an HMAC signature, a plain-language question, options, free-form reply instructions, consequence, and a signed workbench link.
- [ ] Poll only the decision thread and allowlisted sender. Normalize option text or free-form body into `decision.resolved`; late or duplicate replies become correlated no-ops.
- [ ] Retry rate limits with bounded exponential backoff. Authentication drift triggers connector rediscovery and repair; SMTP and extracted OAuth credentials are forbidden substitutes.

### Task 13: Add the One-Hour Hermes WhatsApp Escalation Through the Slice

**Files:**
- Create: `orchestrator/notifications/escalation.py`
- Create: `orchestrator/notifications/hermes_whatsapp.py`
- Create: `orchestrator/hermes/decision_reply.py`
- Modify: `orchestrator/process/supervisor.py`
- Test: `tests/test_decision_escalation.py`
- Test: `tests/test_hermes_whatsapp.py`

**Interfaces:** `EscalationService.tick(now) -> EscalationResult`; `HermesWhatsAppChannel.send`; `HermesDecisionReply.handle` posts a signed resolution to the local workbench API.

- [ ] Keep the channel disabled until the owner pairs `hermes whatsapp` by QR, an owner chat is allowlisted, and outbound plus inbound correlation tests pass.
- [ ] Outbound uses `hermes send --to whatsapp:<owner-chat> --json`; success requires the Hermes message receipt, not exit code alone.
- [ ] Inbound uses a dedicated owner-only Hermes gateway profile and decision-reply handler. Reject messages from other chats, invalid signatures, unknown decisions, or already-resolved decisions.
- [ ] Make the decision eligible at `decision.blocking_since + 3600 seconds` and send one WhatsApp escalation on the first supervisor tick at or after that timestamp only if no channel has resolved it. Do not create reminders or quiet-hour logic.
- [ ] A WhatsApp resolution closes the Gmail duplicate; a later Gmail reply is logged as duplicate and does not change the answer.
- [ ] Add supervisor cadence of 60 seconds only after the benefit gate passes; idle ticks write no per-minute receipt and perform indexed due-decision queries only.

### Task 14: Integrated Three-Capability Acceptance, Learning, and Cutover

**Files:**
- Create: `orchestrator/workbench/learning.py`
- Create: `tests/acceptance/test_intent_orchestrator_release.py`
- Create: `scripts/prove_intent_orchestrator_release.ps1`
- Modify: `docs/STATUS.md`

**Interfaces:** `LearningService.propose(discrepancy) -> LearningProposal`; `EvaluationRunner.replay(proposal_id) -> EvaluationReceipt`; promotion requires owner approval.

- [ ] Run one owner task that retrieves relevant context from indexed transcripts, recommends and uses all four services where competent, preserves disagreement, and reaches a blocking owner decision.
- [ ] Resolve one decision by Gmail and a separate timed test decision by WhatsApp after a clock-injected one-hour boundary. Verify cross-channel duplicate closure.
- [ ] Inject duplicate replies, a conflicting late reply, Gmail delay, missing connector, service crash, partial file append, stale frame, mid-task correction, server restart, and unavailable embedding service. Verify containment and repair state for each.
- [ ] Generate learning proposals from owner corrections, overridden service recommendations, missed evidence, and failed repairs. Replay against real transcript-derived evaluations and adversarial fixtures; do not promote automatically.
- [ ] Rebuild every projection from the event ledger and compare canonical snapshots. Verify zero new bounded-contract rows and no visible-UI automation process or scheduled action.
- [ ] Keep the old route available in shadow/read-only mode until all acceptance predicates pass. Then make `/workbench` the default nontrivial entry path and mark the predecessor components quarantined in the UI and status document.

## Release Acceptance Predicates

- Every event sequence replays to the same snapshot after restart.
- Every material decision is owner-resolved or explicitly delegated before dependent execution.
- Every native question from Claude, Codex, Hermes, and Gemini is correlated to one central decision or rejected as nonmaterial with a recorded reason.
- Every correction pauses only dependency-linked work.
- Transcript coverage names every accessible source, its cursor, last success, exclusions, missing surfaces, and semantic-coverage state.
- Exclude and Forget pass content-leak tests across FTS, embeddings, summaries, API responses, caches, and replay.
- Gmail and WhatsApp receipts are read back from their real connector/gateway surfaces.
- No external action lacks task authority, idempotency, delivery receipt, and outcome readback.
- No completion state exists without a named consumer and evidence for every required success node.
- No predecessor bounded-outcome table receives a new row.
- No visible application ping/typing automation exists.
- The desktop ratios, border dragging, expansion, and mobile tabs pass the real browser matrix.
- The integrated proof receipt points to all three governed capability-build runs and their independent verification receipts.

## Execution Order and Review Gates

1. Tasks 1-9 bootstrap the slice through the current development path.
2. Task 10 is the first capability built through the slice.
3. Task 11 is the second capability built through the slice.
4. Tasks 12-13 are the third capability built through the slice.
5. Task 14 is the integrated release gate and cutover.

At each task boundary: run the named tests, inspect only the task diff, run the relevant existing regression tests, record the evidence in the workbench once available, and do not advance while a required predicate is open. Because the current worktree contains owner changes, commits may include only new files and isolated hunks that have been proven not to absorb unrelated modifications; otherwise leave the verified diff uncommitted for owner reconciliation.

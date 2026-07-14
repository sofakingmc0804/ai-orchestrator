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
- Predecessor quarantine is staged first and becomes active only after the live replacement receipt passes; permanent deletion remains owner-approved work.
- Transcript bodies, chunks, summaries, snippets, embeddings, and hidden reasoning never enter immutable workbench events, frame text, logs, receipts, repair descriptions, learning proposals, or migration backups. Immutable records hold opaque transcript references/checksums only; purgeable content lives in a separate store unless the owner explicitly confirms a new paraphrased task artifact.
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
    event_schema_version: int
    event_id: str
    task_id: str
    sequence: int
    event_type: str
    actor: EventActor
    branch_id: str
    cause: EventCause | None = None
    caused_by: str | None = None
    command_id: str
    command_sequence: int
    frame_version: int
    payload: dict[str, Any]
    idempotency_key: str
    created_at: str
    prior_checksum: str
    checksum: str

class FrameNode(BaseModel):
    node_id: str
    node_key: str
    task_id: str
    branch_id: str
    frame_version: int
    supersedes_node_id: str | None = None
    kind: FrameNodeKind
    text: str
    value: JsonValue
    status: Literal["confirmed", "invalidated"]
    depends_on: tuple[str, ...] = ()
    provenance_event_ids: tuple[str, ...] = ()

class FrameState(BaseModel):
    task_id: str
    branch_id: str
    frame_version: int
    nodes: tuple[FrameNode, ...]
    edges: tuple[FrameEdge, ...]
    state_checksum: str

class FrameSnapshot(BaseModel):
    state: FrameState
    head_sequence: int
    head_event_checksum: str

class DecisionRequest(BaseModel):
    decision_id: str
    task_id: str
    branch_id: str
    revision: int
    queue_order: int
    kind: DecisionKind
    semantic_identity: str
    question: str
    options: tuple[DecisionOption, ...]
    free_form_allowed: bool = True
    recommendation_option_id: str | None = None
    recommendation_reason: str | None = None
    changed_outcome: str
    positions: tuple[OutcomePosition, ...]
    materiality: MaterialityAssessment
    consequence_if_unresolved: str
    affected_node_keys: tuple[str, ...]
    tier: DecisionTier
    state: DecisionState
    provenance: Provenance
    source_occurrences: tuple[QuestionOccurrence | ReplyOccurrence, ...]
    pending_interpretation: StructuredDecisionInterpretation | None = None
    resolution: DecisionResolution | None = None

class ServiceRun(BaseModel):
    run_id: str
    task_id: str
    service: Literal["claude", "codex", "hermes", "gemini", "local"]
    role: str
    frame_version: int
    branch_id: str
    revision: int
    state: Literal["queued", "starting", "running", "waiting_owner", "paused_dependency", "repairing", "verifying", "interrupted", "complete", "failed", "canceled"]
    output_validity: Literal["current", "stale", "reverification_required"]
    native_identity: NativeIdentityEnvelope | None = None
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
5. **Owner edits:** an edit becomes a proposal. The workbench shows affected frame nodes, branches, runs, evidence, and immutable external-action references before confirmation; it never invents mutable artifact/receipt state that has no authority surface.
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
- Create: `orchestrator/state/migration_runner.py`
- Create: `orchestrator/state/migrations/0002_workbench_core.sql`
- Create: `tests/fixtures/state_v1.sql` or an equivalent immutable v1 database builder
- Modify: `orchestrator/state/store.py`
- Modify: `pyproject.toml`
- Test: `tests/test_workbench_migrations.py`

**Interfaces:** `await MigrationRunner(settings, migrations_dir=None).apply(db) -> list[int]`; migration 2 creates fully constrained `workbench_events`, `workbench_tasks`, `frame_nodes`, `frame_edges`, `frame_proposals`, `decision_requests`, `service_runs`, `evidence_refs`, `learning_proposals`, `quarantined_components`, and projection metadata.

- [x] RED first: prove fresh upgrade, immutable-v1 sentinel preservation, idempotent second initialize with no backup, partial-statement rollback, durable repair receipt after rollback, readable pre-migration backup, checksum/catalog drift rejection, concurrent initialize exactly once, backup-failure abort, Windows-safe published backup, integrity/foreign-key checks, and wheel-installed SQL resource loading.
- [x] Freeze `schema.sql` as the version-1 bootstrap. Backfill an existing version-1 row as `name='legacy_baseline'`, `status='applied'`, `checksum=NULL`; require immutable non-null checksums for version 2 onward. Existing guarded compatibility alterations finish before the post-v1 runner.
- [x] Define migration-2 columns, keys, indexes, and foreign keys before implementation. At minimum: task-scoped unique event sequence and idempotency; actor, branch, cause, frame version, payload, prior checksum, checksum, timestamps; projection name/sequence/state checksum; indexed frame-edge traversal; decision affected nodes/options/consequence/provenance; and service-run native session plus exact input-node IDs.
- [x] Completion evidence separates observation frame/head from the immutable success-node revision, anchors exact event IDs plus sequences, permits only valid branch ancestry through fork sequence/frame cutoffs, and is immutable except for one complete invalidation. Native recovery identity is all-or-none and hierarchically scoped. Quarantine activation references an immutable, component-scoped, successful live replacement proof plus a distinct typed activation event; proof/state cannot be fabricated, reused, rewritten, deleted, or moved backward.
- [x] Discover only `NNNN_name.sql`; reject malformed/duplicate/gapped catalogs, unknown applied versions, missing applied files, changed applied checksums, and transaction-control statements. Package both baseline and numbered SQL files in the wheel.
- [x] Use a bounded busy timeout and `BEGIN IMMEDIATE`, then re-read migration state under the lock. Back up through a separate read connection before schema writes, validate the backup, execute complete SQLite statements individually without `executescript()`, and commit the pending batch atomically.
- [x] On failure: roll back, confirm the transaction ended, persist one deduplicated repair item in a new transaction, retain but never auto-restore the backup, and raise a typed migration exception. Use a Windows-safe unique temporary backup name and `os.replace()` only after handles close and validation passes.
- [x] Run `pytest tests/test_workbench_migrations.py -v` plus the existing state-store suite; expected result: all migration, packaging, concurrency, idempotency, backup, rollback, repair, catalog, and checksum tests pass.

### Task 2: Implement the Append-Only Event Ledger and Projections

**Reopened foundation gate:** adversarial Task-2 deletion proved that event ordinals alone cannot preserve original command cardinality. Keep accepted migration 2 frozen. Before Task 2 can complete, separately reviewed `0003_workbench_command_manifests.sql` must add the immutable, endpoint-bound, checksummed command authority defined in `.superpowers/sdd/task-1-command-manifest-addendum.md`. Verification must bind manifests and events bidirectionally before retry, append, snapshot, replay, or rebuild. Task 2 remains paused during that repair.

**Foundation integration status:** migration 3 is approved for integration after strict type-affinity and registry-vector repairs, 45 focused tests, 20/20 concurrency, an independent controller run, and two reviewer approvals. Task 2 is active again. Acceptance still requires manifest-first writer integration, staged in-transaction semantic verification, corruption negative controls, and the full repository gate.

**Files:**
- Create: `orchestrator/workbench/models.py`
- Create: `orchestrator/workbench/events.py`
- Create: `orchestrator/workbench/projector.py`
- Create: `orchestrator/workbench/store.py`
- Test: `tests/test_workbench_events.py`

**Interfaces:** `WorkbenchStore.create_task(task_id, title, command_id, actor) -> WorkbenchEvent`; `WorkbenchStore.append(task_id, command_id, draft, *, expected_frame_version=None) -> WorkbenchEvent`; `WorkbenchStore.append_batch(task_id, command_id, drafts, *, expected_frame_version=None) -> tuple[WorkbenchEvent, ...]`; `WorkbenchStore.fork_branch(task_id, command_id, parent_branch_id, branch_id, at_sequence, actor) -> WorkbenchEvent`; `WorkbenchStore.snapshot(task_id, branch_id="main", at_sequence=None, verify=True) -> WorkbenchSnapshot`; `WorkbenchStore.verify_ledger(task_id) -> LedgerVerification`; pure `Projector(registry).replay(visible_events, *, task_id, branch_id, at_sequence=None) -> WorkbenchSnapshot`; atomic `Projector.rebuild(store, task_id) -> tuple[ProjectionHead, ...]`.

Task 2 publishes and rebuilds only canonical `projection_metadata`; immutable normalized frame/decision/run/evidence tables are not disposable projections. Its production event catalog is bounded to typed `task.created@1` and `branch.forked@1`, with an injectable typed registry for tests and Task 3 extension. Drafts contain only caller intent fields; the store generates every authority field. `(task_id, command_id)` identifies the whole retry, ordinal keys are domain-separated canonical hashes, and partial/changed retries fail closed. Registry-declared frame effects replace event-name inference; Task 2 permits at most one exact-version `CONFIRM` per batch. Forks and causes must be visible at the exact branch ancestry boundary. Historical snapshots never publish metadata; rebuild locks, verifies, replays every branch, and atomically upserts only `projection_metadata`.

Batches are nonempty and single-branch. `EventCause` is a typed classification string. Fork cutoffs must include the visible task creation event. The store owns ancestry filtering and gives pure replay only verified visible events. Projection publication time is derived from the visible head event, not wall clock. Task 2 leaves the task-global frame-version column frozen at zero and derives branch versions from replay. The checksum flatly includes every migration envelope column except `checksum`, including both cause fields and both command fields.

- [x] Prerequisite gate: Task 1 must prove immutable event-row guards, task-scoped sequence/idempotency uniqueness, same-task causal references, branch-aware projection metadata, valid-JSON checks, normalized `service_run_inputs`, and every branch/provenance/checksum field required here. Do not retrofit migration 2 silently.
- [x] RED first for: missing task/branch; two-store concurrent appends; exact idempotent replay versus changed-content conflict; full immutable-envelope tampering including non-head rows; invalid/future/cross-task causes; canonical key-order equality; non-finite/invalid/unknown payloads; fault/cancellation/reducer rollback; atomic multi-event correction and retry; rebuild repair/idempotency/race; historical and ancestral branch replay; stale frame versions; sibling isolation; per-connection foreign keys/busy timeout; and byte-identical live/rebuilt snapshots.
- [x] Define typed `EventActor`, store-generated `EventDraft` envelope fields, complete `WorkbenchEvent`, `ProjectionHead`, `WorkbenchSnapshot`, verification results, and typed failures. Add `prior_checksum`, `event_schema_version`, cause, command ID, and command sequence to the public event model. Register the bounded Task-2 types with typed structural payloads and deterministic reducers; use an injectable registry for later extension. Reducers never infer intent, frame effects, or dependencies from wording.
- [x] Use a dedicated configured SQLite connection and one explicit transaction; never build atomic ledger work on `_StateStoreDbFacade`, which opens and commits separate connections. Sequence is task-global and contiguous across branches. Pin a named genesis checksum constant and one canonical JSON representation: UTF-8, sorted keys, compact separators, `ensure_ascii=False`, normalized UTC timestamps, and rejected non-finite numbers.
- [x] Hash every immutable stored event-envelope field except `checksum`, plus every command-manifest field except `manifest_checksum`, with explicit nulls and frozen canonical domains. Verify storage classes, canonical registry payloads, the full event chain, command ranges/cardinality/endpoints/drafts/idempotency, topology, exact causes, and frame transitions before retry/append/snapshot/replay/rebuild. This detects unrecomputed or partially coherent corruption; external authenticity still requires an independently anchored or signed head.
- [x] Under `BEGIN IMMEDIATE`, resolve an exact retry or raise `IdempotencyConflict`, verify existing authority, stage one immutable command manifest plus its events, reverify the complete staged transaction, apply pure reducers and normalized projections, and commit once. A mixed, partial, changed, or semantically inconsistent command fails closed.
- [x] Derive frame versions in the store: confirmed frame mutations increment after an expected-version check; non-frame events inherit; proposals do not advance; forks inherit the parent version at the fork sequence. A branch replay includes ancestors only through each fork and excludes siblings.
- [x] Separate pure hypothetical replay, consistent authoritative current/historical snapshot, and atomic metadata-only projection rebuild. Rebuild verifies the ledger/manifests and republishes disposable metadata without modifying authority; it locks and head-compares so a concurrent append cannot be lost.
- [x] Run `pytest tests/test_workbench_events.py -v`, `pytest tests/test_workbench_migrations.py -v`, the state-store suite, and the full repository suite. Completion requires atomic append/batch, exact idempotency, corruption detection, branch history, historical replay, and rebuild equality from a fresh database.

### Task 3: Build Shared Frame, Dependency Graph, Decisions, and Corrections

**Files:**
- Create: `orchestrator/state/migrations/0004_intent_authority_guards.sql`
- Create: `orchestrator/workbench/runtime.py`
- Create: `orchestrator/workbench/frame.py`
- Create: `orchestrator/workbench/decisions.py`
- Create: `orchestrator/workbench/materiality.py`
- Modify: `orchestrator/workbench/store.py`
- Modify: `orchestrator/workbench/models.py`
- Modify: `orchestrator/workbench/events.py`
- Modify: `orchestrator/workbench/projector.py`
- Test: `tests/test_workbench_domain_authority.py`
- Test: `tests/test_shared_frame.py`
- Test: `tests/test_decision_queue.py`

**Interfaces:** branch-aware `FrameService.propose_change`, `confirm_change`, `reject_change`, `fork_from`, and `activate_branch`; `DecisionService.enqueue_native_question`, `reorder`, `resolve`, and `supersede`; `MaterialityGate.evaluate(candidate, snapshot) -> MaterialityAssessment` or typed `UndeclaredOutcomeError`.

**Controller contract closure:** `.superpowers/sdd/task-3-brief.md`, `task-3-preflight.md`, and `task-3-event-contract.md` are normative. Task 3 first adds migration 4, a bounded `WorkbenchRuntime`/`CommandParticipant` authority layer inside the accepted command transaction, pre-allocation domain validation, event-backed task/branch caches, normalized-domain verification, strict public models, literal hash domains, and exact payload/reducer contracts. `core` is a reserved store-owned event authority; every non-core event maps to one runtime participant. No caller SQL callback, regex/text inference, projection-only repair, or service-level transaction is permitted. Migration 2 and 3 remain byte-identical.

- [ ] Foundation RED first: migration-4 precondition/guards including pending-interpretation and discriminated append-only question/reply occurrence storage, runtime participant ownership, omitted/failed/canceled/tampered participant rollback, selected-versus-inactive branch task-global transition replay, normalized-domain row/event disagreement, storage/JSON canonicality, restart equality, Task-1/2 regression, wheel loading, and concurrency.

- [ ] Prerequisite gate: Tasks 1-2 must align public models and schema; support atomic event batches, exact branch ancestry/time travel, concurrent proposals against one base, immutable node revisions plus stable `node_key`, same-task graph/input references, exact run-input revisions/frame version, branch decision queues, structured materiality/provenance, preview checksums, and byte-identical replay/rebuild.
- [ ] RED first for the grouped cases: public node round-trip, canonical tuple order, and text-independence; immutable revision/supersession; concurrent proposals; complete impact preview; stale version/checksum; atomic multi-operation confirmation; dependency-only cycle/propagation semantics; cross-task rejection; historical fork/branch isolation; selective correction/run pause and incomplete-manifest repair; completed-run output reverification, branch-scoped evidence invalidation, and immutable receipt history; correction rollback; ASK/default/ignore/declaration-required; wording-invariant materiality; structural duplicate identity with preserved disagreement; one-active queue/reorder/revision/selected-branch cache rules; urgent queued free-form/interpretation/resolution; and exact option/open-repeat/late-conflict replay.
- [ ] Treat `node_id` as one immutable revision and `node_key` as the stable concept across revisions and branch ancestry. `text` is human display; structured `value`, explicit edges, outcome deltas, authority nodes, and provenance drive behavior. No reducer or gate parses wording to infer intent, dependency, authority, or materiality.
- [ ] Define edge relations `depends_on`, `alternative_to`, `supports`, and `contradicts`. Only `depends_on` is acyclic and propagates invalidation; traversal runs in reverse from changed prerequisite to dependents. Evidence positions remain visible and do not imply dependency unless an explicit edge says so.
- [ ] Proposal creation does not advance frame version; multiple proposals may share a base. Store base sequence/version/state checksum and preview checksum. Proposal and confirmation payloads carry `FrameState` without future ledger authority; the store constructs `FrameSnapshot` only after event allocation. Confirmation requires the exact base and preview, advances the frame once, and atomically supersedes the complete sorted set of pending peer proposals using a separate confirmation-time token outside the frozen impact hash.
- [ ] Historical snapshots are read-only. Mutating history requires an explicit fork. Children inherit ancestry only through the fork boundary; later parent/sibling events and corrections do not leak across branches. Branch activation is an event and preserves every inactive branch queue/run/history.
- [ ] Confirm corrections through one atomic `append_batch`: supersede replaced revisions; create immutable invalidation tombstones for only transitive dependents in the selected branch; pause only nonterminal runs whose exact input revisions intersect; bind run/evidence mutations to owner-reviewed tokens and peer proposals/selected-task cache to confirmation-time tokens; route missing manifests or pause failures to Repairing; degrade completed run output to requiring re-verification and insert branch-scoped evidence invalidation overlays. Task 3 does not invent mutable artifact/receipt state: immutable receipts remain history and usefulness follows the producing run/evidence authority. Never represent irreversible external action as undone.
- [ ] Materiality evaluates declared `OutcomeDelta` structures across intent, success, consequence, authority, cost, owner-visible UX, external action, and irreversibility. `ASK` requires materially different undelegated outcomes; `DISPLAY_DEFAULT` requires a reversible declared default within authority; `IGNORE` requires structural identity/no-op. Missing or contradictory declarations emit `question.declaration_required` back to the source service and never reach the owner queue.
- [ ] Canonical semantic identity uses task, branch, sorted affected logical node keys, and canonical position structures whose deltas use the fixed eight-dimension order and exclude position IDs. Regex/raw text hashes cannot merge. Embeddings may retrieve candidates only. Preserve every exact source question, native ID, service/run/event provenance, option, recommendation/reason, outcome delta, and conflicting position.
- [ ] Enforce one ACTIVE decision per active branch, a full ordered open queue with optimistic queue revision, atomic next activation after resolve/supersede, and notification urgency independent of queue position. Recommendations remain visible but unselected.
- [ ] Preserve free-form owner text exactly and never silently map it to an option. A service may propose a structured interpretation and preview while the decision remains active or blocking/critical queued; pending interpretation is stored separately from final resolution and resolution completes only after an `accepted=true` confirmation, while `accepted=false` is an interpretation-rejection event. Original provenance is immutable; every accepted decision question/reply occurrence appends to a discriminated canonical history. Native-question identity and content are hashed separately: every repeated identity is an immutable `duplicate|conflict` observation event and cannot alter prior authority. Resolving payloads never contain their own future event ID; read models derive it from the enclosing event. Repeated open replies and conflicting late channel replies are retained and cannot overwrite accepted authority.
- [ ] Carry each complete `ReplyOccurrence` in its event so exact timestamp, provenance, classification, native checksum, original occurrence, and canonical append are replayable without inference. Publish and enforce the exact reason-bound ordinary/correction run matrices, governed native-envelope NULL-to-complete rule, output-validity degradation, typed `service_run.receipt_recorded` binding to exact run revision/frame/input checksum, immediate-predecessor completion rule, and event-derived timestamps from `task-3-event-contract.md`.
- [ ] Run `pytest tests/test_shared_frame.py -v`, `pytest tests/test_decision_queue.py -v`, Tasks 1-2 tests, the state-store suite, and the full repository suite. Completion requires exact branch/history replay, atomic corrections, continued unrelated work, no undeclared/nonmaterial owner questions, and preserved disagreement/provenance across restart and rebuild.

### Task 4: Implement Adaptive Guided Intake and Service-Team Recommendation

**Execution dependency:** build Task 5 protocol clients first behind a disabled runtime gate; then implement Task 4 against injected managed interpretation providers; enable both only after their joint live acceptance test. The legacy regex/one-shot router may remain a post-frame routing hint but cannot decide sufficiency, materiality, consequence, authority, or owner-question need.

**Files:**
- Create: `orchestrator/workbench/intake.py`
- Create: `orchestrator/workbench/recommendation.py`
- Modify: `orchestrator/routing/brain.py`
- Test: `tests/test_workbench_intake.py`

**Interfaces:** `IntakeEngine.begin`, `apply_resolution`, and `reassess` with command IDs and optimistic queue/frame versions; `TeamRecommender.recommend(frame, CapabilityInventorySnapshot, TeamOverrides) -> ServiceRecommendation`.

- [ ] Define `TaskCreateRequest`, `InterpretationCandidate`, `IntakeAssessment`, immutable `CapabilityInventorySnapshot`, `AuthorityGrant`, `TeamOverrides`, `ServiceRecommendation`, and `IntakeResult`. Authority mode controls who decides details; it is not permission to mutate, call connectors, send, submit, or perform external/irreversible actions.
- [ ] Sufficiency is structural: no unresolved, undelegated material outcome difference remains across intent, success, consequence, authority, cost, owner-visible experience, external action, or irreversibility; success/readback surfaces and a current competent route are known. Do not use confidence thresholds, keywords, regex, or model self-assertion.
- [ ] For low-consequence work, use one competent structured interpreter unless material alternatives appear. For consequential work, obtain independent managed Claude and Codex candidates. Preserve both exactly as proposals, reject unsupported authority/capability claims, merge only structurally identical nodes, and route unresolved outcome differences through `MaterialityGate` one active question at a time.
- [ ] Auto-start only when the frame is sufficient, every required action is authorized or read-only/reversible, no external/irreversible action is implied, the capability snapshot is current, and no owner team lock is violated. Emit `frame.inferred` and `intake.sufficient`; never add a timer or fake confirmation.
- [ ] Discover against a recorded bounded universe: verified inputs/authority; adapter contracts/health/models/quota/accounts; installed skills/plugins plus live callable connector/MCP proof; local sources/transcript indexes; public research only for a declared evidence gap. Stop when each required capability has a current competent route or a named unavailable/repair record.
- [ ] Recommend the smallest competent role-to-service/model team with alternatives, rejected candidates, quota/cost/authority consequences, capability revision, and unavailable mechanisms. Recommendations never grant authority or preselect an owner choice. Surfaces sharing one provider/account capacity are not double-counted.
- [ ] RED first for complete simple auto-start, terse material ambiguity, complex-but-complete no-ceremony, wording-invariant outcomes, visible material disagreement, invalid model JSON, unproved connector claims, exact free-form replay, stale revisions, restart queue identity, correction supersession, team overrides/locks, stale inventory revalidation, unavailable connector repair, no unnecessary public research, ungoverned import isolation, and a production scan proving no regex decides authority/materiality/sufficiency.

### Task 5: Add Managed Claude and Codex Sessions

**Files:**
- Create: `orchestrator/adapters/claude_session.py`
- Create: `orchestrator/adapters/codex_app_server.py`
- Create: `orchestrator/adapters/session.py`
- Create: `orchestrator/mcp/workbench_server.py`
- Modify: `orchestrator/adapters/builtins.py`
- Modify: `pyproject.toml`
- Test: `tests/test_managed_claude_session.py`
- Test: `tests/test_codex_app_server.py`

**Interfaces:** `ManagedSessionAdapter` plus concrete `RunEnvelope`, serializable `ServiceRunHandle`, `DecisionResolution`, and `TranscriptExport`. Build protocol clients behind a disabled runtime gate before Task 4; existing one-shot dispatch remains separate.

- [ ] Implement truthful lifecycle: queued → starting → running → waiting_owner/paused_dependency/repairing → running → verifying → complete; interruption routes through interrupted/repairing; owner cancellation ends canceled. Provider turn completion never completes the workbench task.
- [ ] Persist generated run/native identity before the first work turn. `start()` is idempotent; pause interrupts only an active dependent turn; resume targets the exact stored identity and explicitly injects the confirmed new frame; cancel preserves transcript/receipt. Crash recovery never selects “latest” and never silently switches service/model.
- [ ] Claude: use pinned `claude-agent-sdk` with one `ClaudeSDKClient` per run and the installed subscription-authenticated Claude Code CLI. Route `AskUserQuestion` and permission prompts through `can_use_tool`; do not unconditionally allow the question tool. Remove inherited `ANTHROPIC_API_KEY` only for the Claude Max child route and never fall back to paid API. If the SDK is unavailable, enter repair—no raw-CLI substitute.
- [ ] Claude restart reconciliation: a live callback can wait without arbitrary timeout; after process death, preserve the central decision, mark the native request superseded/interrupted, then resume the exact session with the confirmed resolution/current frame in a new turn. Child-agent question limitations remain visible.
- [ ] Codex: supervise one app-server stdio JSON-RPC process with a connection multiplexer; initialize/initialized with experimental user-input negotiation; persist `thread/start` ID before `turn/start`; resume exact thread; interrupt exact turn; correlate every request by task/run/frame/thread/turn/request; treat `serverRequest/resolved` as authoritative cleanup and never send a late answer to another request.
- [ ] Normalize provider multi-question groups into ordered central decisions, apply materiality per member, retain group/native provenance, show recommendations unselected with equal free-form input, and answer the native group only when every still-material member resolves. Cleared native requests are superseded; late/conflicting replies remain conflict evidence.
- [ ] Implement Claude Desktop/Cowork as attach-only MCP, never visible-UI control. Use hashed, random ≥256-bit task/run/frame-scoped expiring single-use attach tokens. Expose only attach, frame read, owner question, evidence emission, and run close. Claim native conversation binding only if live MCP metadata proves a stable ID.
- [ ] Mark direct CLI/Desktop sessions `direct_unmanaged`; indexing does not grant authority or task binding. Reconciliation is a separate owner-confirmed proposal. Bound every provider line/message/stderr/transcript/MCP payload; malformed/oversized/unknown events enter repair, not silent discard. Dedupe by provider-native IDs plus run identity, not content hash.
- [ ] RED first for shared idempotent lifecycle/restart/cancel/export, Claude question/approval separation/restart/auth/SDK absence, Codex initialization/request binding/resolution/interrupt/crash/overload/live connector proof, MCP token replay/scope/staleness/close idempotency, and a guard proving no visible Claude/Codex UI automation.
- [ ] Joint acceptance with Task 4: natural request → guided intake → visible disagreement → one native question → owner resolution → exact-session continuation → correction pause/resume → real authority-surface verification.

### Task 6: Expose the Workbench API and Live Event Stream

**Files:**
- Create: `orchestrator/ui/workbench_api.py`
- Create: `orchestrator/ui/workbench_security.py`
- Create: `orchestrator/ui/workbench_uploads.py`
- Create: `orchestrator/ui/workbench_transcription.py`
- Modify: `orchestrator/ui/server.py`
- Modify: `orchestrator/config.py`
- Modify: `pyproject.toml`
- Test: `tests/test_workbench_api.py`
- Test: `tests/test_workbench_security.py`
- Test: `tests/test_workbench_uploads.py`

**Interfaces:** the listed REST/SSE endpoints plus `POST /api/workbench/tasks/{task_id}/messages`; every mutation carries idempotency key, expected frame version, expected head sequence, stable tab client ID, and payload. Accepted edited voice text enters only through `/messages` with `source=voice`.

- [ ] Register the router without changing existing routes. `GET task` returns canonical snapshot, frame/head versions, checksum/ETag, active branch, sync/capability summary, and missing mechanisms. Use concrete pause/resume/cancel paths and typed plain-language errors with retryability/recovery.
- [ ] Scope idempotency to principal, method, path, task, and canonical request hash. Exact retry returns original status/body/event IDs; changed content returns typed 409. Every mutation checks both frame version and head sequence; queue reorder also supplies the complete expected open order. A stale response returns current snapshot without discarding the owner draft.
- [ ] SSE uses UTF-8 `text/event-stream`, sequence `id`, event type, numeric retry, and 15-second comment heartbeats. Start from `after`/valid `Last-Event-ID`; repeatedly read `sequence > cursor` from SQLite before waiting to close the snapshot/live race. Ignore exact duplicates; gaps/schema/checksum failures force resnapshot. Revocation emits `session.revoked` and closes/rejects reconnect; disable caching/proxy buffering.
- [ ] Uploads require declared length, enforce it before and during streaming, use private temporary files outside static roots, count/hash while streaming, inspect extension/content and safe structured containers, fsync, then atomically publish by generated ID. Reject executables/scripts/shortcuts/macros/uninspected archives/traversal/decompression bombs; interrupted/rejected work leaves no temp data. Attachment success does not send it to a model.
- [ ] Browser speech interim/final text remains client-only and visibly provisional. Governed local fallback uses bounded audio metadata/storage deleted in `finally`; distinct recoverable states cover permission, browser, model, cancellation, silence, and partial output. Transcription creates no ledger event until the owner selects “Use transcript,” which sends edited text through `/messages`.
- [ ] Default loopback requires Host/Origin, CSRF, and session protection for mutation without login ceremony. LAN stays disabled unless Windows Private scope plus HTTPS or approved encrypted tunnel exists. Exchange a generated ≥256-bit capability once from a URL fragment into `HttpOnly`, `Secure`, `SameSite=Strict` cookie; never query strings/logs/localStorage. Rotation invalidates HTTP and SSE. Add CSP, no-referrer, nosniff, no-store, safe attachment disposition, and text-only rendering.
- [ ] RED first for snapshot/SSE race and reconnect, duplicate/gap handling, heartbeat no-write, restart, concurrent stale mutations, exact/changed idempotency, stale reorder, deceptive/oversize/traversal/macro/bomb uploads and cleanup, local-transcription cleanup/no-event/accept-once, LAN-without-encryption denial, and capability revocation during SSE.

### Task 7: Build the Three-Pane Workbench

**Files:**
- Create: `orchestrator/ui/static/workbench.html`
- Create: `orchestrator/ui/static/workbench.css`
- Create: `orchestrator/ui/static/workbench.js`
- Modify: `orchestrator/ui/server.py`
- Test: `tests/browser/test_workbench_ui.py`

**Interfaces:** `/workbench` serves the page; browser state is derived from the snapshot plus SSE events.

- [ ] Header always shows task title/state, sync state, frame version, branch, pending command, active/paused runs, last applied event, capability gaps, and evidence source; technical IDs remain under expandable details. Conversation owns history/composer/files/voice; Frame owns structured outline/dependencies/alternatives/authority/evidence/success/positions/provenance; Now owns one active decision, consequence, affected work, recommendation reason, equal free-form input, queue, and team controls.
- [ ] At ≥900 CSS px use `30fr 46fr 24fr` with minimums 260/360/220 px. Border-aligned handles expose ≥24 px hit areas without consuming layout width; clamp, normalized persistence, double-click/button reset, full-pane expand/Escape restore, corrupt-preference reset, and internal vertical splitters are required.
- [ ] Every splitter is a focusable ARIA separator with orientation/current/min/max; arrows resize, Shift accelerates, Home/End clamp, Escape restores. Pointer capture prevents stuck drag. Queue actions have Move up/down buttons; drag is optional. Primary touch targets are ≥44 px.
- [ ] Below 900 px keep Conversation/Frame/Now mounted behind an ARIA tablist so drafts, recording, provenance expansion, focus, and stream state survive. Manual keyboard tab activation, sticky Now count/severity badge, structured dependency outline, every desktop action, and 320px/400% reflow without page-level horizontal scroll are required.
- [ ] Apply only contiguous recognized SSE events. Duplicate ignores; gap/unknown/checksum/reconnect ambiguity resnapshots. Show Connecting/Live/Reconnecting/Offline/Access revoked. Preserve drafts/files on reconnect without auto-submit. On 409 show server-versus-local impact with Apply to latest/Keep editing/Discard; never silently overwrite or reapply intent changes.
- [ ] No recommendation starts selected; free-form is visually equal. Frame edits open an impact review before confirmation. Dependency-only pauses are visible. Uploads use semantic progress; pasted images get generated name/description. Mic start/stop is explicit; provisional text is labeled; “Use transcript” is separate and disabled until editable text exists. Model disagreement remains separate positions.
- [ ] Target WCAG 2.2 AA without claiming conformance from automation alone: headings/landmarks, controlled live regions, visible/unobscured focus, predictable restoration, nonvisual graph equivalent, adjacent errors and recovery banner, non-color status, forced colors, text spacing/orientation, reduced motion, safe text/link rendering.
- [ ] Test measured layout and all interactions at 1920×1080, 1440×900, 1280×720, 900×900, 899×900, 768×1024, 390×844, and 320 CSS px; 200%/400% zoom; keyboard-only and no-drag paths; ARIA/focus/status; forced colors/reduced motion; injection; reconnect/ambiguous submit; stale second-tab resolution; 409 frame edit; malformed/duplicate/gapped SSE; and live capability revocation.

### Task 8: Add Completion Verification, Recovery, and Old-System Quarantine

**Files:**
- Create: `orchestrator/state/migrations/0005_completion_authority.sql`
- Create: `orchestrator/workbench/verifier.py`
- Create: `orchestrator/workbench/recovery.py`
- Create: `orchestrator/workbench/cutover.py`
- Create: `orchestrator/governance/quarantine.py`
- Create: `orchestrator/governance/automation_audit.py`
- Modify: `orchestrator/process/supervisor.py`
- Test: `tests/test_workbench_verifier.py`
- Test: `tests/test_workbench_recovery.py`
- Test: `tests/test_workbench_quarantine.py`
- Test: `tests/test_automation_benefit_audit.py`

**Interfaces:** exact-head `CompletionVerifier.verify`; leased `RecoveryService.recover_open_tasks`; `QuarantineRegistry.stage`, `activate`, and `assert_inactive`; `AutomationBenefitAuditor.scan`, `evaluate`, and `quarantine`.

- [ ] Completion evidence is per required success predicate, not one row per node. It records named consumer, exact task/branch/node revision/frame/head, predicate/expected outcome, authority surface/readback method, verifier identity/independence, freshness, observed checksum/value, status pass/fail/conflict/inconclusive, producing run/source event, and invalidation. Model text/logs/screenshots count only when they are the declared authority.
- [ ] If independently mutable artifact or receipt validity is required, migration 5 creates its exact normalized authority, transition guards, verifier, and a new versioned correction event before the UI or completion gate may expose that claim. Until then, Task-3 artifact/receipt usefulness derives only from producing-run output validity and branch-scoped evidence; immutable receipt events remain history.
- [ ] After readbacks, acquire `BEGIN IMMEDIATE`, recheck unchanged branch/frame/head/success revisions, and append `verification.passed` plus `task.completed` atomically. Missing/stale/conflicting/inconclusive/mismatched evidence emits failure, deduplicated changed-mechanism repair, and Task Repairing. No other API/model/adapter may write Complete.
- [ ] Recovery acquires one lease; verifies ledger; rebuilds disposable projections on checksum mismatch (no pending-projection queue); loads nonterminal work; validates exact provider/adapter/account/model/session/thread/turn/request/group/capability handles; reconciles decisions; reads back uncertain external actions before retry; resumes only current inputs; records every reconciliation; starts ordinary supervision only after stability.
- [ ] State recovery is explicit: queued idempotent start; starting reserved-identity reconciliation; running exact reattach/resume; waiting_owner and paused_dependency remain; verifying repeats authority readback; repairing/interrupted uses only a due changed mechanism; complete/failed/canceled never resumes. A vanished native request is superseded while the central decision remains; late replies become conflicts.
- [ ] Task 8 only stages exact predecessor components as `candidate`/`shadow_readonly`. Task 9 live replacement proof atomically activates quarantine, removes dispatch/hook authority, installs insert/update/delete guards on the six exact legacy tables, preserves validated backup/read access/counts/content digest, and never deletes evidence. Recovery repairs cutover forward and never silently restores predecessor authority.
- [ ] Quarantine exact bounded/score-gate components and old proof routes/directories, not broad `hook`/`skill`/`receipt` names. Preserve capability routing, connector discovery, authority guards, artifact predecessor checks, current skill receipts, and the hidden AI Orchestrator watchdog/server.
- [ ] Automation audit covers Scheduled Tasks, internal scheduler, Claude scheduler JSON, Run/RunOnce, Startup, services, permanent WMI consumers, active process ancestry, wrapper scripts, and recent launches. Keep only named-consumer/cadence/useful-receipt/resource-compliant/non-visible-UI automation. A confirmed visible AI ping loop is disabled at every trigger, exact process tree stopped, leases revoked, evidence preserved, non-relaunch proved, and hidden orchestrator health reproved. Weak matches enter repair/review, not destruction.
- [ ] RED first for cross-task/stale evidence, frame-change completion race, corrupt-ledger recovery, double recovery, latest-session selection, improper waiting/paused resume, uncertain external duplicate, identity mismatch, legacy insert/update/delete after cutover, evidence deletion, broad automation false positive, and surviving second trigger.

### Task 9: Bootstrap-Slice Acceptance Gate

**Files:**
- Create: `orchestrator/workbench/bootstrap_gate.py`
- Create: `orchestrator/workbench/acceptance_receipt.py`
- Create: `tests/acceptance/test_bootstrap_slice.py`
- Create: `tests/acceptance/test_bootstrap_receipt_negative_controls.py`
- Create: `scripts/prove_workbench_bootstrap.ps1`

- [ ] Split proof into deterministic and live lanes. Deterministic fault-injected adapters prove replay, normalization, correlation, correction, dependent pause, restart, stale evidence, staged quarantine, and receipt tamper rejection; they never label themselves live. Live proof requires the real workbench UI, Claude/Codex subscription sessions, natural owner task, genuine native material question, owner resolution, retained disagreement, correction/impact preview, dependent pause plus unrelated progress, actual process restart, safe action, authority readback, and verifier-only completion.
- [ ] Remove “if material”: bootstrap proof requires one genuine provider-native normalized question. Playwright may prove DOM/reconnect conformance but cannot impersonate owner judgment or authority readback. Test client, mocks, screenshots, seeded answer, or local logs cannot substitute for the live lane.
- [ ] Restart receipt records distinct pre/post boot/process IDs, event head/frame checksum/decision/native handles, actual governed restart, ledger verification, rebuilt snapshot equality, exact request reconciliation, and no duplicate provider turn/external action.
- [ ] Machine receipt contains schema/receipt/task/branch/build/migration/adapter/capability/browser/event/frame/restart/native identity/decision/correction/pause/unrelated-progress/success-evidence/automation/quarantine/deterministic-suite fields, generated from authoritative surfaces only, then canonical SHA-256 signed. Caller-supplied proof fields are rejected.
- [ ] `core_bootstrap_ready` requires both lanes against the same build/schema and enables only Task 10’s governed-build lane. It activates staged predecessor quarantine after all live predicates; it does not make workbench the global default or claim release completion. `self_hosting_proven` is earned only after Task 10 is built through the slice; Tasks 11–13 then have per-capability gates and Task 14 remains release cutover.
- [ ] Exact live predicates include owner-originated task/frame/authority/consumer/success, distinct Claude/Codex roles and identities surviving restart, one genuine native question with exact answer correlation, visible disagreement, selective correction plus later unrelated event, real process restart, canonical replay equality, no duplicates, fresh evidence for every predicate, atomic unchanged-head completion, deterministic pass, post-proof quarantine activation, unchanged legacy counts/digest, no runnable visible-UI ping automation, fresh hidden-orchestrator health, and signed receipt consumed only by the Task 10 gate.
- [ ] Negative controls reject fake identities, test-client-only UI, screenshots without events, scripted owner choice, no native request ID, changed identity after restart, fake restart, no concurrent unrelated progress, wrong authority surface, pre-readback completion, edited receipt, one-lane-only proof, early Task 10 enablement, early quarantine, post-cutover legacy writes, and remaining UI-ping trigger.

---

### Task 10: Build Transcript Intelligence Through the Slice

**Files:**
- Create: `orchestrator/state/migrations/0006_transcript_metadata.sql`
- Create: `orchestrator/transcripts/base.py`
- Create: `orchestrator/transcripts/identity.py`
- Create: `orchestrator/transcripts/content_store.py`
- Create: `orchestrator/transcripts/policy.py`
- Create: `orchestrator/transcripts/coverage.py`
- Create: `orchestrator/transcripts/reconcile.py`
- Create: `orchestrator/transcripts/watcher.py`
- Create: source-specific Claude Code, Cowork/Desktop metadata, Codex, Gemini, and Hermes modules
- Create: `orchestrator/transcripts/indexer.py`
- Create: `orchestrator/transcripts/search.py`
- Test: `tests/test_transcript_sources.py`
- Test: `tests/test_transcript_index.py`

**Interfaces:** adapters for Claude Code JSONL, Codex active/archived JSONL, legacy Gemini session JSON plus current append-only JSONL, Hermes read-only WAL-consistent SQLite with CLI fallback, and allowlisted Claude Desktop/Cowork metadata plus nested `.claude/projects/**/*.jsonl`. Cloud-only sources remain visible as export-required/unsupported, never falsely complete.

- [ ] Metadata authority DB stores source instances/roots, canonical conversations, physical artifacts/aliases, body-free message identities/revisions, parser/schema fingerprints, adapter cursors, policy versions/exclusions, forget jobs/HMAC tombstones, coverage/health, reconciliation proposals, and embedding provenance. A separate purgeable store owns bodies, chunks, FTS, embeddings, generated summaries, and snippet caches.
- [ ] Enforce the refs-only rule: transcript text/summaries/snippets/hidden reasoning never enter immutable events, frame text, audits, repairs, receipts, logs, backups, or learning. Transcript summaries remain purgeable until owner-confirmed promotion creates a new paraphrased task artifact with disclosed permanence.
- [ ] Identity has three levels: source instance (service/account/adapter/root), HMAC-scoped native conversation key, and physical artifact/alias. Messages use native IDs; synthetic IDs require declared deterministic components and synthetic marking. Missing stable identity enters format repair; never bind by latest, title, proximity, or content similarity. Divergent same-native-ID records remain revisions/conflicts.
- [ ] Index only owner-visible content. Exclude hidden reasoning/thinking/system/developer/auth/internal protocol and nonvisible tool traffic. Tool items require explicit visibility. Every adapter has hidden-reasoning/secret bait fixtures.
- [ ] Cursor by source: JSONL file identity/generation/complete offset/fingerprints/parser/incomplete tail; Gemini native session plus JSON document fingerprint/message IDs for legacy rewrites or JSONL generation/offset for current append-only sessions; Hermes stable IDs/high-water in a read-only WAL-consistent transaction. Empty state-only Gemini artifacts are negative controls, not conversations. Locked files do not advance; truncation starts new generation; moves become aliases; disappearance is retained/missing, not silent deletion.
- [ ] Cowork discovery is bounded and allowlisted: session metadata, `local_<native-id>` directories, nested transcript JSONL only. Never open credentials, audit logs, project/task/attachment/pending-upload/cache/LevelDB/arbitrary sandbox files. Legacy sanitized copies are `derived_copy` aliases/conflicts and do not add coverage.
- [ ] Every document has monotonic policy version. Exclude atomically denies eligibility, increments version, deletes retrieval rows, invalidates caches, and rejects stale in-flight search/context/embedding/summary/learning work. Include creates a new version and remains unavailable until rebuild completes.
- [ ] Forget requires confirmation, immediately denies reads/writes, writes an HMAC tombstone before purge, removes every managed body/chunk/FTS/embedding/summary/cache/temp/WAL/SHM/proposal copy, restarts/compacts secure-delete storage, scans for a canary leak, and only then records completion. Failure remains `forget_pending_repair`; source-service/unmanaged backups stay outside the claim; tombstone blocks reimport.
- [ ] Coverage states distinguish accessible complete/partial, metadata-only, derived copy, unreadable, format changed, cloud export required, unsupported cache, excluded, and forgotten. Reconcile physical artifacts exactly into canonical/alias/excluded/tombstoned/unreadable/malformed/unsupported counts with no remainder. Transcript provenance never proves tool execution, truth, external action, or completion.
- [ ] Direct sessions import ungoverned and unbound. Search may use included history, but task reconciliation requires owner-confirmed proposal showing effects/limits. Only a managed run with pre-persisted exact native identity auto-binds. Transcript references cannot grant authority or satisfy completion.
- [ ] Narrow watchers/manifests only; no recursive Cowork watch and no new Scheduled Task. Initial backfill is resumable/budgeted and separate. Idle benefit gate measures p50/p95 CPU, working-set delta, bytes/files read, DB/WAL growth, duration, and zero new transcript receipt with no delta. Existing supervisor receipt behavior is audited separately in Task 8.
- [ ] RED with synthetic fixtures for partial/malformed/oversized/locked/truncated/rotated/copied/moved/divergent IDs, hidden reasoning, Gemini rewrites, Hermes WAL/schema drift/empty state, Cowork bait, cloud gaps, derived copies, exclusion races, forget canary/tombstone/restart, source conflict, direct-session isolation, and idle resource ceiling.
- [ ] Live acceptance captures a volatile start manifest and accounts for every detected root/artifact/conversation; proves one exact visible canary and absent hidden canary per supported source; validates aliases, coverage gaps, Cowork allowlist nonaccess, excluded/forgotten zero retrieval, and owner-confirmed reconciliation. Build Task 10 through the live slice to earn `self_hosting_proven`.

### Task 11: Build Hermes and Gemini Managed Adapters Through the Slice

**Files:**
- Create: `orchestrator/adapters/acp_client.py`
- Create: `orchestrator/adapters/hermes_session.py`
- Create: `orchestrator/adapters/gemini_session.py`
- Modify: `orchestrator/adapters/session.py`
- Modify: `orchestrator/adapters/builtins.py`
- Modify: `orchestrator/mcp/server.py`
- Modify: `orchestrator/mcp/contracts.py`
- Modify: `orchestrator/governance/subscription_usage.py`
- Modify: `orchestrator/governance/dispatcher.py`
- Modify: `orchestrator/workbench/recommendation.py`
- Modify: the Task 10 Gemini transcript source for both legacy JSON and current JSONL
- Test: `tests/test_acp_client.py`
- Test: `tests/test_hermes_gemini_sessions.py`

**Interfaces:** `AcpClient` owns JSON-RPC framing, session lifecycle, notifications, ACP tool approvals, exact-session cancellation, and resume; both adapters implement `ManagedSessionAdapter`. The shared Task 5 MCP tool `workbench_request_decision` handles intent/material ambiguity and remains distinct from ACP `session/request_permission` tool approval.

- [ ] Readiness is earned in order: `transport_ready`, `session_ready`, `inference_ready`, `decision_ready`, `recovery_ready`, `transcript_ready`, then `task11_capability_ready`. `hermes acp --check`, process launch, initialize, or session creation alone cannot enable either adapter.
- [ ] Hermes keeps its own provider/model/account/billing identity. Do not alias Hermes to Nous or hardcode `local_resource`/repo-coding capability. The observed Hermes 0.18.2 ACP route uses Ollama Cloud `deepseek-v4-pro`; Nous authentication is required only when a selected Nous-specific route actually needs it.
- [ ] Gemini uses `GEMINI_CLI_HOME` and lets the installed CLI own native OAuth refresh. Remove the hardcoded OAuth client/direct token-refresh path from `subscription_usage.py`; API-key, Vertex, gateway, and direct paid API routes remain forbidden defaults. Current Gemini CLI 0.45.2 initialization is only transport/session evidence until a real inference receipt exists.
- [ ] Because neither live ACP surface exposes a universal intent-question method, map model-detected intent/material ambiguity through `workbench_request_decision`. Map ACP `session/request_permission` only to `tool_approval`. Resume either path only after its exact matching central resolution event.
- [ ] Pause/correction cancels the exact native prompt and persists service/session/turn/process-generation identity. Recovery loads that exact session and re-prompts with the current frame; it never selects the latest session. If a Hermes permission request times out or is superseded, cancel the native request but preserve the central decision, then reload and reissue only after exact correlation.
- [ ] Export exact session transcripts and ingest them through Task 10. Empty prior Hermes history and state-only Gemini JSONL are healthy negative controls, not conversations.
- [ ] Capability, model, account, quota, tool, and skill inventories are observed and freshness-bound, never static labels. Team recommendation also uses consequence ceiling and owner overrides and never chooses by majority vote.
- [ ] Test missing executable, broken ACP framing, provider/model/account drift, quota exhaustion, permission timeout, crash, late reply, cancellation, supersession, frame correction, exact-session resume, transcript identity, forbidden paid-route fallback, and every readiness transition.
- [ ] Through the live slice, route Claude to architecture, Codex to implementation, Hermes to runtime audit, and Gemini to adversarial review when their live capability and readiness receipts support those roles. Keep one writer per worktree, preserve genuine disagreement, and never fabricate a four-service result when any service failed to reach the required readiness state.

### Task 12: Build Gmail Decision Delivery and Reply Correlation Through the Slice

**Files:**
- Create: `orchestrator/state/migrations/0007_decision_notifications.sql`
- Create: `orchestrator/notifications/channels.py`
- Create: `orchestrator/notifications/outbox.py`
- Create: `orchestrator/notifications/signing.py`
- Create: `orchestrator/notifications/codex_gmail_bridge.py`
- Create: `orchestrator/notifications/decision_email.py`
- Create: `orchestrator/notifications/gmail_reply_parser.py`
- Create: a protected key-store adapter
- Modify: `orchestrator/skills/gate.py` only through an explicit narrow owner-channel policy exception
- Test: `tests/test_decision_email.py`
- Test: `tests/test_codex_gmail_bridge.py`
- Test: policy-hook, migration, crash/restart, ambiguous-send, parser, and reconciliation cases

**Interfaces:** `CodexGmailBridge.get_profile`, `send_decision`, `search_replies`, and `read_thread`; each returns the real connector tool name, connector result ID, Gmail message/thread ID, and readback.

- [ ] Use Codex app-server `app/list` to prove the installed Gmail app exists, then run a read-only `_get_profile` call and require `owner@example.com` before enabling delivery. Fresh preflight confirmed the current callable connector/profile; the bridge must independently reproduce that event-stream proof.
- [ ] Execute Gmail calls through a narrow Codex app-server connector task. Accept success only when the event stream contains the intended Gmail tool call and a successful connector result; model prose is not a delivery receipt.
- [ ] The repository currently forbids external Gmail send except the GovCon brief. The owner-decision channel remains disabled until an explicit, narrow policy/gate exception names only the authenticated sender profile, `owner@example.com`, decision-message schema, consequence ceiling, rate limit, receipt/readback, and kill switch. Every other recipient or message type is rejected before dispatch; no bypass is allowed.
- [ ] The owner-only capability is state-backed, fresh, and single-use. It requires the exact sender/recipient, no CC/BCC/attachments, active delivery reservation, exact subject/body digest, purpose `owner_decision`, and recorded `reply_message_id` for replies; consume it atomically at invocation. Prompt wording is never authority.
- [ ] The live Gmail connector exposes no custom-header input and no caller idempotency key. Put a versioned signed opaque decision/reply token in the subject/body with the plain-language question, options, exact free-form instructions, consequence, expiry, and signed workbench link. Never claim or test `X-AI-Orchestrator-Decision` support that the authority surface lacks.
- [ ] Reserve the `(decision_id, notification_generation, channel)` delivery atomically before send. On success, persist connector result ID plus exact Gmail message/thread IDs, then read the Sent message/thread back and validate recipient, token, content digest, and timestamp before recording delivered.
- [ ] A crash or timeout after dispatch is `delivery_uncertain`, never failed. Search/read Sent for the exact signed token and generation; adopt exactly one valid match, surface conflict for multiple matches, and retry only after proved absence. Never blind resend.
- [ ] Poll only the exact decision thread and allowlisted sender. Verify token version/signature/expiry/decision/generation/message/thread identity before retaining the exact free-form body or option. Accepted, duplicate, conflicting-late, invalid, and superseded replies are distinct immutable events; only the first valid unresolved authority changes the decision.
- [ ] Reject outbound/Sent/draft/auto-reply/forwarded-token/quoted-only messages and require an unseen Gmail message ID after delivery time with bounded skew. Deterministic MIME/signature stripping preserves exact owner text. Only a machine-carried signed `option_id` is an option selection. Plain text, even if exactly equal to an option label, remains free-form and requires `frame_interpretation_confirmation` before resolution.
- [ ] Use a durable outbox with immutable attempts and per-account/thread reply cursors. Do not use the existing local `.eml` subscriber or any cursor that advances past an unclassified/failed external delivery or reply.
- [ ] Correlation and workbench capabilities use separate token purposes, key IDs, nonce hashes, expiry/single-use rules, constant-time verification, and protected DPAPI/ACL-backed keys. Secret material never enters events, logs, receipts, transcripts, or model context; old keys remain verify-only only for their bounded lifetime.
- [ ] Retry rate limits with bounded exponential backoff. Authentication drift triggers connector rediscovery and repair; SMTP and extracted OAuth credentials are forbidden substitutes.

### Task 13: Add the One-Hour Hermes WhatsApp Escalation Through the Slice

**Files:**
- Create: `orchestrator/notifications/escalation.py`
- Create: `orchestrator/notifications/hermes_whatsapp.py`
- Create: `orchestrator/hermes/decision_reply.py`
- Create: a dedicated Hermes `pre_gateway_dispatch` owner-decision plugin
- Create: dedicated Hermes profile installer/validator
- Create: persistent WhatsApp send journal and authority-readback adapter
- Modify: `orchestrator/process/supervisor.py`
- Test: `tests/test_decision_escalation.py`
- Test: `tests/test_hermes_whatsapp.py`

**Interfaces:** `EscalationService.tick(now) -> EscalationResult`; `HermesWhatsAppChannel.send`; `HermesDecisionReply.handle` posts a signed resolution to the local workbench API.

- [ ] Current live preflight found Hermes WhatsApp stopped, unpaired, unconfigured, and without targets. Keep the channel disabled until the owner pairs a dedicated owner-only profile by QR, one exact owner chat/sender is allowlisted, durable secrets are provisioned, and live outbound plus inbound correlation/readback passes.
- [ ] Outbound uses `hermes send --to whatsapp:<owner-chat> --json`. Reserve a persistent send journal before dispatch and require the exact Hermes message receipt plus authority readback. Exit code and the in-memory outbound echo tracker are insufficient across restart. Ambiguous send remains `delivery_uncertain` until readback or explicit owner repair; never blind resend.
- [ ] Inbound uses a dedicated owner-only `pre_gateway_dispatch` plugin before authentication/model dispatch. It validates exact profile/chat/sender/message/reply token/signature/expiry/generation, posts one idempotent resolution to the local workbench, records readback, and returns `skip` so a decision reply consumes no model turn. `fromOwner` alone is never identity proof.
- [ ] Live Hermes currently catches `pre_gateway_dispatch` exceptions and continues normal dispatch. Channel enablement requires a supported, version-pinned fail-closed profile boundary and conformance test proving a missing/crashed/corrupt decision plugin cannot reach an LLM. Do not patch installed Hermes source ad hoc; use a supported plugin/profile extension with install and rollback receipts.
- [ ] Reject and retain as security/conflict events messages from other chats/senders/profiles, invalid or expired signatures, unknown/superseded decisions, replayed generations, ambiguous quoted context, and already-resolved decisions.
- [ ] Persist `blocking_since_utc` for one continuous active blocking episode. Reorder, retry, restart, timezone, and DST never reset it. At exactly `blocking_since_utc + 3600 seconds`, claim under `BEGIN IMMEDIATE`, recheck immediately before invocation, and use the unique `(decision, channel, kind)` delivery identity. Resolution/supersession cancels uninvoked claims; an invocation race may become `late_delivered` but never reopens the decision. Do not create reminders or quiet-hour logic.
- [ ] A WhatsApp resolution closes the Gmail duplicate; a later Gmail reply is logged as duplicate and does not change the answer.
- [ ] Any accepted workbench/Gmail/WhatsApp resolution uses one compare-and-swap transaction and cancels sibling deliveries not yet invoking. Exact later answers are duplicates; different later answers are conflicts; both remain visible evidence and neither overwrites authority.
- [ ] Add supervisor cadence of 60 seconds only after the benefit gate passes; idle ticks write no per-minute receipt/audit row and perform indexed due-decision queries only. Refactor the current always-write tick behavior before enablement.

### Task 14: Integrated Three-Capability Acceptance, Learning, and Cutover

**Files:**
- Create: `orchestrator/workbench/learning.py`
- Create: `orchestrator/workbench/release_gate.py`
- Create: `tests/acceptance/test_intent_orchestrator_release.py`
- Create: `tests/acceptance/test_intent_orchestrator_failure_matrix.py`
- Create: `tests/acceptance/test_intent_orchestrator_accessibility.py`
- Create: `tests/acceptance/test_intent_orchestrator_security.py`
- Create: `scripts/prove_intent_orchestrator_release.ps1`
- Modify: `docs/STATUS.md`

**Interfaces:** `LearningService.propose(discrepancy) -> LearningProposal`; `EvaluationRunner.replay(proposal_id) -> EvaluationReceipt`; `ReleaseGate.evaluate(manifest) -> ReleaseDecision`; promotion and cutover require owner approval.

- [ ] Run one owner task that retrieves relevant context from indexed transcripts, recommends and uses all four services where competent, preserves disagreement, and reaches a blocking owner decision.
- [ ] Resolve one decision by Gmail and a separate timed test decision by WhatsApp after a clock-injected one-hour boundary. Verify cross-channel duplicate closure.
- [ ] Inject duplicate replies, a conflicting late reply, Gmail delay, missing connector, service crash, partial file append, stale frame, mid-task correction, server restart, and unavailable embedding service. Verify containment and repair state for each.
- [ ] Generate learning proposals from owner corrections, overridden service recommendations, missed evidence, and failed repairs. Replay against real transcript-derived evaluations and adversarial fixtures; do not promote automatically.
- [ ] Rebuild every projection from the event ledger and compare canonical snapshots. Verify zero new bounded-contract rows and no visible-UI automation process or scheduled action.
- [ ] Run the release matrix across fresh install, migrated owner database, malformed/corrupt state, two concurrent clients, slow/disconnected browser, expired/revoked session, connector revocation, provider identity drift, quota exhaustion, process kill at every durable boundary, machine restart, disk-full/write denial, clock shift, and dependency unavailability. Every case must either complete with authority evidence or fail closed into an exact resumable repair state without duplicated external action.
- [ ] Run real browser accessibility and responsive proof at 320 CSS px, 400% zoom, keyboard only, touch/no-drag path, reduced motion, screen-reader labels/live regions, forced colors/high contrast, and pane-boundary extremes. Verify all three panes remain mounted or equivalently reachable with full action parity.
- [ ] Run loopback/LAN security proof for Host/Origin/CSRF/session fixation, capability URL leakage, SSE authorization/revocation/replay gaps, upload traversal/type/size bombs, stored/rendered content injection, transcript secret/hidden-reasoning canaries, connector confusion, replayed replies, forged receipts, and least-authority external actions.
- [ ] Run bounded performance/benefit proof for event replay/rebuild, transcript idle watcher, SSE reconnect, decision queue, adapter recovery, one-hour supervisor tick, database/WAL growth, CPU, memory, file reads, and no-work behavior. A background mechanism with no measured consumer benefit is removed before cutover.
- [ ] Keep the old route in staged shadow/read-only state and preserve its baseline count/digest/backup until every acceptance predicate and independent review passes. Cutover is one atomic owner-approved release event with a signed manifest, exact rollback handle, and post-cutover readback. Quarantine activation and `/workbench` defaulting happen only after that event; rollback never rewrites or discards the new ledger.
- [ ] Learning remains proposal-only: exact discrepancy, source evidence, affected policy/skill/route, competing interpretations, evaluation corpus/version, before/after results, safety impact, rollback, and owner decision. Recursive evaluation may generate a new proposal or repair task, but no model may silently promote prompts, skills, policies, allowlists, authority, timing, provider routes, or security boundaries.
- [ ] Self-healing is bounded state-machine repair, not autonomous improvisation: classify the failed authority surface, preserve exact handles and idempotency, try a changed verified mechanism within the existing grant, re-read the real surface, and resume only from the last proved boundary. Expansion of authority, cost, external mutation, or interpretation returns to the owner decision queue.

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
- The release manifest names the exact build, schema, policy/skill inventory revisions, source coverage, live provider identities, browser matrix, security matrix, failure matrix, performance budget, rollback handle, owner approval, and post-cutover readback; edited, stale, partial, or mismatched manifests fail closed.
- No test count, process exit, model prose, screenshot, or generated receipt is accepted alone. Every consequential predicate is tied to the real authority surface and independently read back.

## Execution Order and Review Gates

1. Tasks 1-9 bootstrap the slice through the current development path.
2. Task 10 is the first capability built through the slice.
3. Task 11 is the second capability built through the slice.
4. Tasks 12-13 are the third capability built through the slice.
5. Task 14 is the integrated release gate and cutover.

At each task boundary: run the named tests, inspect only the task diff, run the relevant existing regression tests, record the evidence in the workbench once available, and do not advance while a required predicate is open. Because the current worktree contains owner changes, commits may include only new files and isolated hunks that have been proven not to absorb unrelated modifications; otherwise leave the verified diff uncommitted for owner reconciliation.

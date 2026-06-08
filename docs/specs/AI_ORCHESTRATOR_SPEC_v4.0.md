# AI Operating Layer / Orchestrator
# Specification v4.0
## Date: 2026-06-05
## Author: Matt Couch / Example Consulting
## Supersedes: v1.0 (app-wrapper), v2.0 (client-app-router), v3.0 (process-orchestration daemon)

---

# 0. IDENTITY

**The Orchestrator is a standalone user-facing application.** It has its own UI, its own command surface, its own notification spine, and its own state. It sits above Windows and above every AI app on the machine. Matt interacts with the Orchestrator directly. The Orchestrator interacts with every other AI app on Matt's behalf.

**The Orchestrator is not:**
- Not a wrapper around Claude Desktop, Codex Desktop, or any single AI app
- Not an MCP server pretending to be the spine (MCP is one of several adapter protocols, not the architecture)
- Not a passive monitoring dashboard
- Not a chat client competing with Claude Desktop
- Not a Claude skill, a CLAUDE.md config, or anything that lives inside another product

**The Orchestrator is:**
- A first-class application Matt opens, types into, drags files onto, and gets work back from
- A capability registry, routing engine, and adapter host
- A selection-driven work executor in v1; an autopilot platform in v2+
- Universal in design (any machine, any OS), Windows-first in delivery

---

# 1. ARCHITECTURAL PRINCIPLES

These are invariants. Any design decision that violates one of these is wrong and must be revised.

**P1. The Orchestrator is the primary user surface.** Matt does not type intent into Claude Desktop, Codex Desktop, or any other app to drive orchestration. He types into the Orchestrator. Other apps are operated by the Orchestrator on his behalf.

**P2. Every managed app gets its own adapter speaking its native protocol.** Claude Desktop speaks MCP, so its adapter is an MCP server. Codex CLI speaks subprocess args, so its adapter is a subprocess controller. Ollama speaks HTTP, so its adapter is an HTTP client. There is no universal "spine" protocol. There is a universal adapter interface inside the Orchestrator, and many per-app adapter implementations behind it.

**P3. All managed services are first-class.** No "primary" and "secondary." No "v1 services" and "deferred services." Adapters are built for every discovered AI service on the machine in the same pass. Claude Desktop, Claude Code CLI, Codex Desktop, Codex CLI, Ollama (HTTP and CLI), Copilot, Gemini, Hermes, OpenClaw, and LM Studio all receive adapters together.

**P4. Universal first, Windows first to ship.** Core code uses OS-portable abstractions (pathlib, psutil, asyncio, aiosqlite, fastapi). Per-OS specifics (process discovery details, notification surface, shell integration) live behind a `PlatformAdapter` interface. Windows implementation ships first. macOS and Linux implementations added later without core changes.

**P5. Capability-driven routing, never service-name routing.** The router selects a service by matching the task's required capability against the live capability registry. Adding a new service is registering a new contract. The router code does not change.

**P6. Subscription-included cloud, local-free local, never metered API.** No API keys are created or used. Cloud capability comes through subscription client apps (Claude Max, ChatGPT, Copilot, Gemini Free/Paid subscriptions). Local capability comes through Ollama and LM Studio. Metered API endpoints are forbidden by policy and unsupported by adapter design.

**P7. Selection-driven in v1, autopilot infrastructure built but gated off.** v1 ships a selection interface in four modes (browser, drag-drop, right-click, command palette) and a capability registry. Autopilot mechanics (watched folders, standing-order policies, event triggers) are implemented but disabled by default. Autopilot enables per-folder, per-policy, only after the underlying mechanics are proven in sandbox.

**P8. Strict approval defaults, loosen per folder.** Critical and high consequence tiers require human approval before any external action. Medium requires approval before send/commit. Low auto-executes with notification. Per-folder policy files can loosen these defaults for specific cases. No global override.

**P9. Capability targets are the unit of progress.** Implementation is measured by what the system can demonstrably do at any moment, not what was constructed. A capability target is satisfied when it passes its acceptance test, regardless of which "phase" it belonged to.

**P10. The notification file feed is the channel-agnostic spine.** Every notification is written to a single structured stream (`notifications.jsonl`). Tray notifications, email drafts, and any future channels (Telegram, SMS, Slack) are subscribers to that stream, not parallel implementations.

---

# 2. LAYER MODEL

```
+-----------------------------------------------------------+
|                       MATT (User)                          |
|  Types into Orchestrator. Drags files onto Orchestrator.   |
|  Right-clicks files to send to Orchestrator. Reviews and   |
|  approves work in Orchestrator. Receives notifications     |
|  from Orchestrator via tray, email, file feed.             |
+----------------------------+------------------------------+
                             |
+----------------------------v------------------------------+
|                  ORCHESTRATOR (this spec)                  |
|                                                            |
|  Standalone app. Manual start via shortcut in v1.          |
|  Universal architecture, Windows first.                    |
|                                                            |
|  +------------------------------------------------------+  |
|  |  USER SURFACE                                        |  |
|  |   - Chat/command input                               |  |
|  |   - Project/file browser                             |  |
|  |   - Drag-drop target                                 |  |
|  |   - Command palette (global hotkey)                  |  |
|  |   - Shell context-menu integration                   |  |
|  |   - Selection queue                                  |  |
|  |   - Activity feed                                    |  |
|  |   - Approval/review panels                           |  |
|  |   - Service health and quota panel                   |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  INTENT INTERPRETER                                  |  |
|  |   Parses chat input + selection context into a       |  |
|  |   structured Intent. Asks clarifying questions when  |  |
|  |   needed. Decomposes large intents into a task graph |  |
|  |   via dispatched planning calls.                     |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  CAPABILITY REGISTRY                                 |  |
|  |   Live registry of every adapter's offered           |  |
|  |   capabilities, costs, latency, quality ratings,     |  |
|  |   current health. Contracts in YAML. Hot-reloadable. |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  ROUTING ENGINE                                      |  |
|  |   Matches Intent capability requirements against     |  |
|  |   capability registry. Applies consequence-tier      |  |
|  |   policy, quota budget, project preferences. Picks   |  |
|  |   adapter. Logs decision with reason.                |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  DISPATCHER + ENVELOPE                               |  |
|  |   Wraps Intent in a context envelope (parent intent, |  |
|  |   project, prior context refs, expected output       |  |
|  |   shape, deadline, consequence tier). Hands envelope |  |
|  |   to selected adapter. Tracks lifecycle.             |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  ADAPTER LAYER (one per managed app)                 |  |
|  |   - claude-desktop-mcp     (MCP server adapter)      |  |
|  |   - claude-code-cli        (subprocess adapter)      |  |
|  |   - codex-desktop          (MCP / subprocess hybrid) |  |
|  |   - codex-cli              (subprocess adapter)      |  |
|  |   - ollama-http            (HTTP API adapter)        |  |
|  |   - ollama-cli             (terminal subprocess)     |  |
|  |   - copilot-gh             (gh CLI adapter)          |  |
|  |   - copilot-vscode         (extension adapter)       |  |
|  |   - gemini-cli             (CLI adapter)             |  |
|  |   - hermes-agent           (subprocess + gateway)    |  |
|  |   - openclaw-gateway       (HTTP gateway adapter)    |  |
|  |   - lm-studio              (OpenAI-compatible HTTP)  |  |
|  |   Each implements common interface: dispatch,        |  |
|  |   health_probe, capabilities, cost_estimate.         |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  PROCESS MANAGER                                     |  |
|  |   Process discovery, grouping, health monitoring,    |  |
|  |   lazy-launch of managed services (Hermes,           |  |
|  |   OpenClaw, LM Studio), crash detection, restart     |  |
|  |   policy per service.                                |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  I/O INGESTION                                       |  |
|  |   Log file tailing (Claude main.log, MCP server logs,|  |
|  |   Codex daily logs, Chrome native host, Cowork VM),  |  |
|  |   HTTP API polling (Ollama /api/ps, OpenClaw         |  |
|  |   /health, LM Studio /v1/models), CLI probing        |  |
|  |   (claude auth status, gh auth status). Normalizes   |  |
|  |   to unified event stream.                           |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  STATE STORE (SQLite, WAL mode)                      |  |
|  |   services, capabilities, adapters, intents,         |  |
|  |   dispatches, receipts, projects, standing_orders,   |  |
|  |   quota_ledger, repair_queue, working_memory,        |  |
|  |   notifications, discovery_log, audit_log            |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  SCHEDULER                                           |  |
|  |   Internal asyncio cron. Owns all recurring work.    |  |
|  |   Migrated from Claude Cowork scheduled-tasks.       |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  NOTIFICATION SPINE                                  |  |
|  |   notifications.jsonl is the source of truth.        |  |
|  |   Subscribers: Windows tray (win10toast or native    |  |
|  |   shell), Email drafter (writes Gmail drafts via     |  |
|  |   IMAP/SMTP or Gmail MCP), file feed tail (for any   |  |
|  |   future channel).                                   |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  AUTOPILOT (built, gated off in v1)                  |  |
|  |   File watchers, standing-order policy engine,       |  |
|  |   event triggers, decomposition planner. All wired,  |  |
|  |   all dormant. Enabled per-folder by explicit policy |  |
|  |   file with explicit approval.                       |  |
|  +------------------------------------------------------+  |
|  +------------------------------------------------------+  |
|  |  PLATFORM ADAPTER (per-OS)                           |  |
|  |   Windows implementation in v1. macOS, Linux         |  |
|  |   implementations behind same interface.             |  |
|  +------------------------------------------------------+  |
+----------------------------+------------------------------+
                             |
+----------------------------v------------------------------+
|                  MANAGED AI SERVICES                       |
|                                                            |
|  Claude Desktop  Claude Code CLI  Codex Desktop  Codex CLI |
|  Ollama (HTTP + CLI)  GitHub Copilot  Gemini CLI           |
|  Hermes Agent  OpenClaw  LM Studio                         |
|  (Plus any future service discovered or registered)        |
|                                                            |
+----------------------------+------------------------------+
                             |
+----------------------------v------------------------------+
|                       OPERATING SYSTEM                     |
|         Process scheduler, filesystem, network, GPU        |
+-----------------------------------------------------------+
```

---

# 3. CAPABILITY TARGETS (the "A" of A+B+C)

These are the unit of progress. Each is independently testable, independently valuable, and orderable based on leverage rather than construction sequence.

**CT-01: Service Inventory.** At any moment, the Orchestrator can answer "what AI services are alive on this machine right now, with PIDs, memory, uptime, and health?" via UI panel and via internal API.

**CT-02: Capability Inventory.** At any moment, the Orchestrator can answer "what capabilities are currently available across all live services, with their cost class, latency band, and quality rating?"

**CT-03: Activity Stream.** At any moment, the Orchestrator can answer "what has every AI service done in the last N minutes/hours?" with a unified, chronological, filterable, searchable event feed normalized across all sources.

**CT-04: Auth and Quota State.** At any moment, the Orchestrator can answer "what is the auth state of each provider and what is the remaining quota in the current rate-limit window?"

**CT-05: Selection Capture (4-mode).** Matt can select work targets via (a) the in-app project/file browser, (b) drag-drop onto the Orchestrator window, (c) right-click "Send to Orchestrator" in Windows Explorer, or (d) a global-hotkey command palette. Selections accumulate in a Selection Queue.

**CT-06: Intent Capture.** Matt types intent into a command input. The Orchestrator parses it into a structured Intent (verb, object, constraints, deadline). If ambiguous, the Orchestrator asks one clarifying question via in-app prompt before proceeding.

**CT-07: Dispatch Single Intent.** Given a selection + intent, the Orchestrator selects the right adapter, dispatches the work, captures the result, writes a receipt, and surfaces the output. End-to-end round trip from typed intent to delivered output.

**CT-08: Dispatch Decision Transparency.** Every dispatch records a routing decision with reason: which capability was required, which services matched, which was chosen and why, what was rejected and why. Visible in the Activity Stream.

**CT-09: Output Delivery Convention.** Every dispatch writes output to a predictable location per project: `{project_root}/ORCHESTRATOR_OUTPUT/{YYYY-MM-DD}/{intent_id}/` with `result.{ext}`, `receipt.json`, `context.json`. Output path returned in the UI and notification.

**CT-10: Approval Gate.** For any dispatch flagged as consequence-tier high or critical (or any send/commit at medium), the Orchestrator surfaces an approval card in the UI with diff/preview and waits for Matt's explicit approve/reject before external action. Pending approvals also appear in the notification feed.

**CT-11: Notification Spine.** Every notification (completion, failure, approval-needed, system event) is written to `notifications.jsonl`. The Windows tray subscriber surfaces toasts. The email subscriber drafts a Gmail message. The in-app feed shows the live stream. Adding a new channel is implementing one subscriber, no core changes.

**CT-12: Adapter for every discovered service.** Each of the 11 named services has a working adapter implementing the common interface (`dispatch`, `health_probe`, `capabilities`, `cost_estimate`). Adapter registration is data, not code.

**CT-13: Project Discovery.** Orchestrator scans known project roots (Codex trusted projects, VS Code recent workspaces, `~/Documents/`, `D:\SharedRoot\`, Cowork project folders, anything with `.git`, `CLAUDE.md`, `AGENTS.md`, `package.json`) and registers projects with consequence-tier classification.

**CT-14: Per-Project Policy.** Each project can have a policy file declaring routing preferences, approval thresholds, allowed/forbidden services, output destination overrides. Dispatching in a project context applies that project's policy.

**CT-15: Scheduler.** Internal asyncio cron runs recurring tasks defined in the state store. UI to add, edit, pause, and trigger tasks. All previous Cowork scheduled tasks migrate here.

**CT-16: Failure Recovery.** Failed dispatches retry with exponential backoff (3 attempts), fall back to next capability-matching service, and on exhaustion create a repair queue entry plus a notification.

**CT-17: Quota Awareness.** Per-provider rate-limit windows tracked in the quota ledger. Router refuses dispatch when remaining budget below tier-appropriate threshold. UI shows current quota state per provider.

**CT-18: Working Memory Per Project.** Per project, the Orchestrator maintains a working memory record: last activity, in-flight intents, recent outputs, pending approvals, current open threads. Queryable via UI and API.

**CT-19: Autopilot Infrastructure (Gated).** File watchers, standing-order policy engine, event triggers, and decomposition planner are implemented and unit-tested. Default state: disabled globally. Enable per folder via policy file with explicit `autopilot: enabled` flag.

**CT-20: Cross-OS Bones.** Core code passes `mypy` and `pytest` on Windows in v1 delivery. Per-OS adapter interface defined. macOS and Linux implementations stubbed (raise NotImplementedError) but interface stable.

**CT-21: Universal Service Registration.** A new AI service can be added to the system by writing one adapter file conforming to the adapter interface plus one capability contract YAML. No changes to router, dispatcher, or core. Demonstrated by adding a synthetic test service.

**CT-22: Selection-to-Output Round Trip Under 60 Seconds.** For a typical local-routable task (e.g., classify text, OCR document), end-to-end latency from selection + intent submit to delivered output is under 60 seconds on the Dell hardware.

**CT-23: Crash Survival.** Restart the Orchestrator process at any moment. All state restored from SQLite. In-flight dispatches resume or are marked interrupted with a repair queue entry. No data loss.

**CT-24: Audit Trail.** Every adapter action, every routing decision, every state change is recorded in the audit log. Sufficient for after-the-fact reconstruction of any work the Orchestrator performed.

---

# 4. OUTCOME MILESTONES (the "B" of A+B+C)

What Matt experiences. Capability targets compose into these.

**M1. I can open one app and see every AI process, every running model, every recent action, in real time.**
Composed of: CT-01, CT-02, CT-03, CT-04.

**M2. I can pick anything on my machine, type what I want done, and the Orchestrator either does it or asks one clarifying question.**
Composed of: CT-05, CT-06, CT-07, CT-08, CT-09.

**M3. The Orchestrator never sends or commits anything externally without my approval until I trust it enough to loosen per-folder.**
Composed of: CT-10, CT-14, CT-19.

**M4. When something breaks, I find out via tray, email, and in-app feed within seconds, with a specific repair action.**
Composed of: CT-11, CT-16.

**M5. I can add a new AI service to the system in an evening by writing one adapter and one contract.**
Composed of: CT-12, CT-21.

**M6. All my recurring work happens in one scheduler, with one place to look at history.**
Composed of: CT-15, CT-03, CT-24.

**M7. I can move this Orchestrator to my MSI, a fresh laptop, or a Linux box and it works.**
Composed of: CT-20.

**M8. I never burn through Claude Max or ChatGPT quota by accident.**
Composed of: CT-17.

---

# 5. SERVICE CONTRACTS AND ADAPTER SPEC (the "C" of A+B+C)

## 5.1 Adapter Interface

Every adapter implements this Python interface:

```python
class Adapter(Protocol):
    name: str
    service_group: str
    protocol: Literal["mcp", "subprocess", "http", "gateway", "ext"]

    async def health_probe(self) -> HealthState: ...
    async def capabilities(self) -> list[CapabilityContract]: ...
    async def cost_estimate(self, intent: Intent) -> CostEstimate: ...
    async def dispatch(self, envelope: Envelope) -> DispatchResult: ...
    async def cancel(self, dispatch_id: str) -> bool: ...
```

`HealthState`: `{state: healthy|degraded|unhealthy|unknown, last_probe_at, probe_latency_ms, message}`.

`CapabilityContract`: declarative description of one thing the adapter can do.

`CostEstimate`: `{billing_class, expected_tokens_in, expected_tokens_out, expected_latency_ms}`.

`Envelope`: `{intent_id, parent_intent_id, project_id, inputs, prior_context_refs, expected_output_shape, consequence_tier, deadline, approval_token}`.

`DispatchResult`: `{success, output_payload, output_path, tokens_in, tokens_out, errors, follow_up_intents}`.

## 5.2 Capability Contract YAML (per service)

Stored at `adapters/{service_name}/contract.yaml`. Hot-reloaded.

```yaml
adapter: claude-code-cli
service_group: claude
billing_class: subscription_included
provider: anthropic
auth_source: claude_oauth
quota_window:
  duration: "5h"
  limit_units: "tokens"
  limit_count: null   # discovered, not declared
capabilities:
  - id: complex_coding
    rating_instruction: 5
    rating_quality: 5
    latency_band: "10-60s"
    consequence_max: critical
  - id: long_form_analysis
    rating_instruction: 5
    rating_quality: 5
    latency_band: "30-120s"
    consequence_max: critical
  - id: cross_file_refactor
    rating_instruction: 5
    rating_quality: 4
    latency_band: "60-300s"
    consequence_max: high
health_probe:
  type: cli
  command: ["claude", "--version"]
  expect_exit_code: 0
  timeout_s: 10
```

## 5.3 Adapter Inventory (v1)

| Adapter | Protocol | Service Group | Primary Capabilities |
|---|---|---|---|
| `claude-desktop-mcp` | MCP server hosted by Orchestrator | claude | session_chat, skill_invocation, scheduled_task_proxy |
| `claude-code-cli` | subprocess | claude | complex_coding, long_form_analysis, cross_file_refactor |
| `codex-desktop` | MCP if/when available + subprocess fallback | codex | session_chat, plugin_invocation |
| `codex-cli` | subprocess | codex | creative_writing, alternative_reasoning, headless_dispatch |
| `ollama-http` | HTTP API on localhost:11434 | ollama | local_classification, local_embedding, local_ocr, local_chat, local_coding |
| `ollama-cli` | subprocess | ollama | terminal_chat, model_management, interactive_local_session |
| `copilot-gh` | `gh copilot` CLI | copilot | code_suggestion, shell_command_explain |
| `copilot-vscode` | VS Code extension (when reachable) | copilot | in_editor_completion, in_editor_chat |
| `gemini-cli` | `gemini` CLI (when configured) | gemini | google_models_chat, multimodal_when_supported |
| `hermes-agent` | subprocess `hermes run` + optional gateway | hermes | self_learning_task, skill_creation, persistent_agent_session |
| `openclaw-gateway` | HTTP at localhost:18789 (lazy-launched) | openclaw | multi_channel_messaging, channel_agent_turn |
| `lm-studio` | HTTP OpenAI-compatible (lazy-launched) | lm-studio | alternative_local_models, models_not_in_ollama |

Each adapter ships in v1. None are deferred. "Applicable use case" routing for Hermes (self-learning, persistent skill work) and OpenClaw (multi-channel messaging) is encoded in their capability contracts so the router picks them when those capabilities are required.

**Ollama thinking models.** For local reasoning models that expose `thinking` capability, use `/api/chat` as the default adapter path. When the requested output is plain content rather than an explicit reasoning trace, send top-level `think: false`. Verified local case: `gemma4:12b` is a copied alias of pulled `gemma4:12b-it-qat`; `/api/chat` with `think: false` returned content, while default thinking spent the prediction budget in `message.thinking`.

## 5.4 Routing Decision Algorithm

```
def route(intent: Intent, project: Project, quota: QuotaState) -> RoutingDecision:
    # 1. Determine required capability from intent
    required_caps = intent.required_capabilities()

    # 2. Find adapters whose contracts match required capability
    candidates = capability_registry.match(required_caps)

    # 3. Filter by project policy (allowed/forbidden services)
    candidates = project.policy.filter(candidates)

    # 4. Filter by consequence tier (adapter consequence_max >= intent.consequence)
    candidates = [c for c in candidates if c.consequence_max >= intent.consequence]

    # 5. Filter by health (drop unhealthy, allow degraded with note)
    candidates = [c for c in candidates if c.health != "unhealthy"]

    # 6. Filter by quota (drop adapters with insufficient remaining quota)
    candidates = [c for c in candidates if quota.permits(c, intent)]

    # 7. Score: prefer local_free > subscription_included, then by quality rating,
    #    then by latency band fit, then by lowest expected cost
    chosen = score_and_pick(candidates, intent)

    # 8. Build decision record with full reasoning
    return RoutingDecision(
        chosen=chosen,
        considered=candidates,
        rejected=initial_set - candidates,
        reasoning=reasoning_trail,
    )
```

No service names appear in this algorithm. Adding a service does not require touching it.

---

# 6. SELECTION-DRIVEN WORKFLOW (v1)

v1 is selection-driven. Matt picks targets, Matt submits intent, Orchestrator executes. Autopilot is implemented but off.

## 6.1 Four Selection Modes

**Mode 1: In-app project/file browser.**
- Left pane of Orchestrator UI: navigable tree of discovered projects, recently-touched files, watched folders.
- Click to select. Multi-select with shift/ctrl.
- Right-click for actions (Send to Selection Queue, Set Project Policy, Open in Default App, Reveal in Explorer).

**Mode 2: Drag-drop onto Orchestrator window.**
- Any file or folder dragged from Windows Explorer onto the Orchestrator becomes a Selection Queue entry.
- Drop zone is the main Orchestrator window; visual highlight on drag-over.
- Multi-drop supported.

**Mode 3: Windows Explorer right-click "Send to Orchestrator".**
- Shell context-menu integration via registry entries under `HKCU\Software\Classes\*\shell\` (files) and `HKCU\Software\Classes\Directory\shell\` (folders).
- Submenu lists available actions discovered from the capability registry.
- Selection sent to Orchestrator via named pipe or local HTTP POST.

**Mode 4: Global command palette.**
- Hotkey (default `Ctrl+Shift+Space`, configurable) opens an always-on-top palette window.
- Fuzzy-match search across: project names, recent files, registered capabilities, common verbs.
- Pick a target, type intent inline, submit. Palette closes; result delivered via notification spine and main UI.

## 6.2 Selection Queue

- Persistent across Orchestrator restarts (stored in `state.selections`).
- Items can be reordered, removed, grouped.
- An item can be a file, folder, project, URL, snippet, or composite.
- Submitting intent applies to current Selection Queue contents.

## 6.3 Intent Submission

- Main command input below the Selection Queue.
- Free-text intent. Optional structured fields (capability override, consequence override, deadline, output location).
- On submit: Intent Interpreter parses, asks at most one clarifying question if required, otherwise proceeds to routing.
- Result: dispatch_id, expected completion estimate, link to live status in Activity Feed.

---

# 7. AUTOPILOT INFRASTRUCTURE (built, gated off in v1)

Implemented, unit-tested, dormant. Enabled per-folder by explicit policy.

## 7.1 Components

**Watched-paths registry.** Database table: `(path, recursive, pattern, policy_file_path, enabled)`. Default for all entries in v1: `enabled = false`.

**Standing-order policy schema.** YAML file dropped in a folder (e.g., `.orchestrator/standing.yaml`) declares:
```yaml
autopilot: enabled        # required, no default true
triggers:
  - on: file_created
    pattern: "*.pdf"
intent: "triage and file"
required_capability: document_intake
consequence: medium
approval:
  required_for: [send, commit, post]
  notify_on_complete: true
output: "ORCHESTRATOR_OUTPUT/{date}/{intent_id}/"
quiet_hours: []           # 24/7 by global decision
```

**Event sources beyond files.** Built in v1, gated off:
- Gmail label watchers (label `orchestrator-triage` triggers dispatch)
- Calendar event proximity (15 min before any event with `[orchestrator: call-prep]` in description)
- Cowork scheduled task migration shim (receives Cowork-style triggers during migration period)
- Manual `orchestrator trigger <event>` CLI for testing

**Decomposition planner.** When an Intent is too large for direct dispatch (e.g., "respond to this RFP"), a planning dispatch is made to a high-capability service to produce a task graph. Graph then executed node-by-node by the standard dispatcher. Built, gated off until selection-mode trust earned.

**Approval gates.** Every consequence tier has a default gate behavior (Section 1, P8). Standing-order policy can override per folder. Approval requests flow through the notification spine.

## 7.2 Enabling Autopilot

Per folder, in order:
1. Sandbox folder created.
2. Policy file written with `autopilot: enabled` and a single, narrow trigger.
3. Test files dropped; behavior observed.
4. Receipts reviewed for correctness.
5. Approval gates tested by deliberately submitting work that should trigger them.
6. Only after the sandbox folder's behavior is verified does any real project folder receive `autopilot: enabled`.

No global "turn on autopilot" switch. Ever.

---

# 8. STATE STORE SCHEMA (SQLite, WAL mode)

```sql
-- Services discovered or registered
CREATE TABLE services (
    id TEXT PRIMARY KEY,
    name TEXT,
    service_group TEXT,
    adapter_name TEXT,
    protocol TEXT,
    install_path TEXT,
    version TEXT,
    health_state TEXT,
    last_probe_at TIMESTAMP,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Capability contracts per adapter (denormalized from YAML for query)
CREATE TABLE capabilities (
    id TEXT PRIMARY KEY,
    adapter_name TEXT REFERENCES services(adapter_name),
    capability_id TEXT,
    rating_instruction INTEGER,
    rating_quality INTEGER,
    latency_band TEXT,
    consequence_max TEXT,
    billing_class TEXT,
    enabled BOOLEAN DEFAULT TRUE
);

-- Projects discovered or registered
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    root_path TEXT UNIQUE,
    domain TEXT,             -- coding, client_work, personal, etc.
    consequence_tier TEXT,   -- critical, high, medium, low
    policy_file_path TEXT,
    discovered_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Intents submitted by user or autopilot
CREATE TABLE intents (
    id TEXT PRIMARY KEY,
    parent_intent_id TEXT REFERENCES intents(id),
    source TEXT,             -- chat, palette, drag_drop, context_menu, autopilot
    raw_text TEXT,
    parsed_payload JSON,
    project_id TEXT REFERENCES projects(id),
    selections JSON,         -- list of selected items
    consequence_tier TEXT,
    state TEXT,              -- pending, dispatched, awaiting_approval, completed, failed, cancelled
    created_at TIMESTAMP,
    completed_at TIMESTAMP
);

-- Dispatches (one intent may produce multiple dispatches)
CREATE TABLE dispatches (
    id TEXT PRIMARY KEY,
    intent_id TEXT REFERENCES intents(id),
    adapter_name TEXT,
    envelope JSON,
    state TEXT,              -- queued, in_flight, completed, failed, cancelled
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    output_path TEXT,
    error TEXT
);

-- Receipts (one per completed dispatch)
CREATE TABLE receipts (
    dispatch_id TEXT PRIMARY KEY REFERENCES dispatches(id),
    service TEXT,
    capability TEXT,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    cost_class TEXT,
    success BOOLEAN,
    output_summary TEXT,
    full_receipt JSON
);

-- Routing decisions
CREATE TABLE routing_decisions (
    intent_id TEXT REFERENCES intents(id),
    decided_at TIMESTAMP,
    chosen_adapter TEXT,
    candidates_considered JSON,
    candidates_rejected JSON,
    reasoning TEXT
);

-- Standing orders (autopilot, gated)
CREATE TABLE standing_orders (
    id TEXT PRIMARY KEY,
    folder_path TEXT,
    policy_yaml TEXT,
    enabled BOOLEAN DEFAULT FALSE,
    last_fired_at TIMESTAMP
);

-- Quota ledger per provider
CREATE TABLE quota_ledger (
    provider TEXT,
    window_start TIMESTAMP,
    window_duration_seconds INTEGER,
    units_consumed INTEGER,
    units_limit INTEGER,
    last_dispatch_id TEXT REFERENCES dispatches(id)
);

-- Repair queue (failed work needing attention)
CREATE TABLE repair_queue (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP,
    failure_source TEXT,
    failure_detail TEXT,
    suggested_action TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP
);

-- Working memory per project
CREATE TABLE working_memory (
    project_id TEXT PRIMARY KEY REFERENCES projects(id),
    last_activity_at TIMESTAMP,
    in_flight_intents JSON,
    recent_outputs JSON,
    pending_approvals JSON,
    open_threads JSON
);

-- Notifications (mirrors notifications.jsonl)
CREATE TABLE notifications (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMP,
    severity TEXT,           -- info, warn, error, approval_request
    project_id TEXT REFERENCES projects(id),
    intent_id TEXT REFERENCES intents(id),
    title TEXT,
    body TEXT,
    actions JSON,            -- [{label, action_id}]
    delivered_channels JSON, -- [tray, email, ...]
    acknowledged BOOLEAN DEFAULT FALSE
);

-- Discovery log (auth, model, project drift)
CREATE TABLE discovery_log (
    id TEXT PRIMARY KEY,
    ran_at TIMESTAMP,
    scope TEXT,              -- auth, models, projects, services
    delta JSON,
    summary TEXT
);

-- Audit log (every significant action)
CREATE TABLE audit_log (
    id TEXT PRIMARY KEY,
    ts TIMESTAMP,
    actor TEXT,              -- user, adapter_name, scheduler, autopilot
    action TEXT,
    target TEXT,
    detail JSON
);

-- Selection queue (persistent)
CREATE TABLE selections (
    id TEXT PRIMARY KEY,
    added_at TIMESTAMP,
    kind TEXT,               -- file, folder, project, url, snippet, composite
    payload JSON,
    grouped_with TEXT
);
```

All tables persist across Orchestrator restarts. Schema migrations versioned. SQLite file at `~/.orchestrator/state.sqlite`.

---

# 9. NOTIFICATION SPINE

The single source of truth for everything the Orchestrator surfaces to Matt is `~/.orchestrator/notifications.jsonl`. Append-only. One JSON object per line.

**Schema per entry:**
```json
{
  "id": "ntf_2026-06-05T14-03-27_abc123",
  "ts": "2026-06-05T14:03:27.123Z",
  "severity": "approval_request",
  "project_id": "proj_example_example_co",
  "intent_id": "int_2026-06-05_xyz789",
  "title": "Approval needed: send proposal email to Example Co",
  "body": "Draft email composed by Claude Code CLI based on RFP brief. Recipient: ...",
  "actions": [
    {"label": "Approve and send", "action_id": "approve:int_2026-06-05_xyz789"},
    {"label": "Reject", "action_id": "reject:int_2026-06-05_xyz789"},
    {"label": "Open in Orchestrator", "action_id": "open:int_2026-06-05_xyz789"}
  ],
  "channels_requested": ["tray", "email", "in_app"]
}
```

**Subscribers (v1):**
- **Tray subscriber.** Tails the file. For each new entry where `channels_requested` includes `tray`, posts a Windows toast notification with title/body and action buttons.
- **Email subscriber.** Tails the file. For each new entry where `channels_requested` includes `email`, drafts a Gmail message to `owner@example.com` via Gmail MCP (when available) or IMAP/SMTP fallback. Subject prefixed by severity and project.
- **In-app subscriber.** WebSocket to the Orchestrator UI. Live feed in the main window.

**Adding a future channel** (Telegram, SMS, push, Discord) requires one new subscriber process tailing the same file. No core changes.

---

# 10. CROSS-OS PORTABILITY

**Platform-portable libraries** form the core: `pathlib`, `psutil`, `asyncio`, `aiosqlite`, `watchdog`, `fastapi`, `uvicorn`, `pydantic`. None Windows-specific.

**Platform-specific behaviors** behind one interface:

```python
class PlatformAdapter(Protocol):
    def discover_processes(self) -> list[ProcessInfo]: ...
    def register_context_menu(self, actions: list[ContextAction]) -> None: ...
    def show_tray_notification(self, n: Notification) -> None: ...
    def install_startup_entry(self, command: str) -> None: ...
    def known_install_paths(self, service: str) -> list[Path]: ...
    def open_in_default_app(self, path: Path) -> None: ...
```

**v1 implementations:**
- `WindowsPlatformAdapter`: full implementation. Registry edits for context menu, `win10toast` or shell COM for tray, Task Scheduler for optional startup, WindowsApps path discovery, etc.
- `MacPlatformAdapter`: stub raising NotImplementedError. Interface stable.
- `LinuxPlatformAdapter`: stub raising NotImplementedError. Interface stable.

**v2+ adds the macOS and Linux implementations.** No core changes required.

**File paths are always `pathlib.Path`.** No raw string paths in core. No hard-coded path separators. No Windows-only environment variable assumptions (`%APPDATA%` accessed via `os.getenv` with cross-platform fallback).

---

# 11. IMPLEMENTATION SEQUENCING (capability-target-ordered)

Capability targets sequenced by leverage. Each independently testable. None depend on construction of a full "phase."

**Wave 0 (one sitting):** CT-01, CT-02, CT-04. Service inventory, capability inventory placeholder, auth/quota probe. Smallest possible Orchestrator: opens, shows what is running, what is authenticated. Pure observation, no dispatch.

**Wave 1 (selection round trip):** CT-05, CT-06, CT-07, CT-09, CT-12 (first 4 adapters: claude-code-cli, codex-cli, ollama-http, ollama-cli). Selection + intent + dispatch + output. End-to-end manual round trip. Local-first routing works.

**Wave 2 (transparency and control):** CT-08, CT-10, CT-11, CT-23. Dispatch reasoning surfaced. Approval gates wired. Notification spine live. State restores after restart.

**Wave 3 (full adapter set):** CT-12 completed (remaining adapters: claude-desktop-mcp, codex-desktop, copilot-gh, copilot-vscode, gemini-cli, hermes-agent, openclaw-gateway, lm-studio). All 12 adapters functional.

**Wave 4 (projects and policy):** CT-13, CT-14, CT-18. Project discovery. Per-project policy. Working memory per project.

**Wave 5 (scheduling and recovery):** CT-15, CT-16, CT-17, CT-24. Scheduler. Failure recovery chain. Quota awareness. Audit trail complete.

**Wave 6 (autopilot, gated):** CT-19. Autopilot infrastructure implemented. Default disabled. Sandbox testing only.

**Wave 7 (portability and extensibility proof):** CT-20, CT-21, CT-22. Cross-OS interface stable. New-service test (add synthetic adapter end-to-end in under one hour). Selection-to-output latency benchmark.

Order within a wave is flexible. Some targets can shift waves if leverage dictates.

---

# 12. TECH STACK

| Component | Choice | Why |
|---|---|---|
| Language | Python 3.13 | Already installed, async-native, psutil and watchdog mature, pydantic for envelopes |
| Async runtime | asyncio | Standard library, sufficient |
| Process | psutil | Cross-platform process inspection |
| Filesystem | pathlib + watchdog | Portable paths, robust file watching |
| State | SQLite via aiosqlite (WAL) | Zero-config, file-based, sufficient for single-machine v1 |
| HTTP server | FastAPI + uvicorn | Async, websocket native, OpenAPI for free, lightweight |
| HTTP client | httpx | Async, modern, used by adapters for Ollama/LM Studio/OpenClaw |
| MCP server (for claude-desktop-mcp adapter) | mcp SDK (Python) | Standard MCP implementation |
| UI shell | PyWebView (HTML/JS UI hosted in Python window) OR Tauri (Rust shell, web UI) OR pure FastAPI + browser tab | Decision deferred to first UI iteration; PyWebView likely for v1 due to zero build chain |
| UI framework | Vanilla HTML/JS or Svelte (no build) for v1; React/Tauri viable for v2 | Keep build chain minimal in v1 |
| Notifications (Windows) | win10toast-click or pywin32 shell COM | Native toasts with action buttons |
| Email | Gmail MCP when available; smtplib/imaplib fallback | Subscription path, no API keys |
| Shell integration (Windows) | Direct registry edits via winreg | No installer required |
| Packaging | PyInstaller or briefcase for shippable binary; pip-installed in dev | Single-file or directory distribution for portability |

No Node build chain. No Docker. No React build chain in v1. No external infrastructure.

---

# 13. FILE STRUCTURE

```
~/.orchestrator/
    state.sqlite                       # state store
    notifications.jsonl                # notification spine
    config.yaml                        # user config (hotkeys, paths, channels)
    logs/
        orchestrator.log               # the Orchestrator's own log
        adapters/{adapter_name}.log    # per-adapter logs

C:\Users\Couch\dev\ai-orchestrator\    # source tree (or wherever installed)
    orchestrator/
        __init__.py
        main.py                        # entry point, opens UI
        config.py
        state/
            schema.sql
            store.py                   # aiosqlite wrapper
            migrations/
        discovery/
            processes.py
            auth.py
            models.py
            projects.py
        ingestion/
            log_tailer.py
            api_poller.py
            cli_prober.py
            event_normalizer.py
        registry/
            capabilities.py
            contracts.py               # contract loader, hot-reloader
        intent/
            interpreter.py             # parse text into Intent
            decomposer.py              # planning dispatch for large intents
        routing/
            engine.py
            scoring.py
            policy.py
        dispatch/
            envelope.py
            dispatcher.py
            retry.py
            fallback.py
        adapters/
            base.py                    # Adapter protocol
            claude_desktop_mcp/
                adapter.py
                contract.yaml
                mcp_server.py
            claude_code_cli/
                adapter.py
                contract.yaml
            codex_desktop/
                adapter.py
                contract.yaml
            codex_cli/
                adapter.py
                contract.yaml
            ollama_http/
                adapter.py
                contract.yaml
            ollama_cli/
                adapter.py
                contract.yaml
            copilot_gh/
                adapter.py
                contract.yaml
            copilot_vscode/
                adapter.py
                contract.yaml
            gemini_cli/
                adapter.py
                contract.yaml
            hermes_agent/
                adapter.py
                contract.yaml
            openclaw_gateway/
                adapter.py
                contract.yaml
            lm_studio/
                adapter.py
                contract.yaml
        process/
            manager.py                 # lazy-launch, health, restart
        scheduler/
            cron.py
            tasks_store.py
        notifications/
            spine.py                   # write to notifications.jsonl + DB
            subscribers/
                tray_windows.py
                email.py
                in_app_ws.py
        autopilot/                     # built, gated off
            watchers.py
            policy_engine.py
            event_sources.py
        platform/
            base.py                    # PlatformAdapter protocol
            windows.py
            macos.py                   # stubs
            linux.py                   # stubs
        ui/
            server.py                  # FastAPI + uvicorn
            ws.py
            static/
                index.html
                app.js
                style.css
            shell_integration/
                windows_registry.py
        cli/
            main.py                    # orchestrator CLI for testing/scripting
    tests/
        adapters/
        routing/
        intent/
        e2e/
    pyproject.toml
    README.md
```

---

# 14. CONSTRAINTS (carried forward, enforced)

- No API keys. Subscription-included cloud or local-free only.
- No heavy build chain. Python + browser-rendered UI in v1.
- Self-discovering: finds environment by reading processes, configs, log files, CLI outputs.
- Above the apps: Orchestrator does not depend on any single AI app being up.
- Persistent state: SQLite survives Orchestrator restart, app restart, machine restart.
- Progressive: each capability target is independently valuable and testable.
- Universal: cross-OS interface from day one, Windows-only implementations in v1.
- Strict approval defaults: no external action without approval at high/critical tier.
- Selection-driven v1: autopilot is built but gated off.

---

# 15. PREDECESSOR RETIREMENT (per CLAUDE.md GATE 6)

**v3.0 of this spec is hereby superseded.** It treated the Orchestrator as a background daemon with a monitoring sidecar that connected primarily through Claude Desktop's MCP slot. That architecture violated principles P1, P2, and P3 of this v4.0 (Orchestrator must be the primary user surface; no MCP-spine fiction; no service tiering).

**v2.0 of this spec is hereby superseded.** It treated the system as a client-app router with the Orchestrator embedded in Claude Desktop. Violated P1.

**v1.0 of this spec is hereby superseded.** It treated the system as an app wrapper. Violated P1, P2, P3.

`MSI_AI_ROSTER_2026-06-04.md`: NOT superseded by this document. Data source for model and provider information.

`DELL_AI_MODEL_ROSTER_2026-06-04.md`: NOT superseded. Data source for the Dell's model registry. The Orchestrator's capability registry will consume from it during initial bootstrap of the `ollama-http` adapter contract.

**Retirement action:** Any file on disk at `C:\Users\Couch\...\AI_GOVERNOR_SPEC_2026-06-04.md` or `C:\Users\Couch\...\AI_OPERATING_LAYER_SPEC_v3.md` (or similar names) must be moved to an `archive/` subdirectory with a `_SUPERSEDED_by_v4.0` suffix when this v4.0 is committed to the workspace. This v4.0 is the sole authoritative spec going forward.

---

# 16. OPEN ITEMS (low-confidence, deferred)

These were not decided in the Q&A rounds and are recorded here so they are not silently invented later:

- **UI shell choice (PyWebView vs Tauri vs FastAPI + browser tab).** Decide at first UI iteration based on what feels right when actually built. All three remain viable.
- **Context menu submenu granularity.** Whether right-click shows raw capabilities or curated common actions. Decide once capability registry is populated.
- **Command palette ranking algorithm.** Fuzzy match implementation and ranking weights. Decide during palette implementation.
- **Decomposition planner consequence cap.** What is the maximum consequence tier an autopilot decomposition is allowed to assign without explicit approval. Probably medium. Decide before enabling autopilot on any non-sandbox folder.
- **Per-project policy file format details.** YAML schema sketched in Section 7. Full schema with all optional fields decided during Wave 4.
- **MSI handoff protocol when added in v2.** Replication strategy for SQLite, dispatch routing across machines, identity reconciliation. Out of scope for v1.

---

# 17. VERSIONING

```
VERSION:      4.0
AUTHOR:       Matt Couch / Example Consulting
LAST_UPDATED: 2026-06-05
CHANGELOG:
  4.0 -- Complete architectural reframe based on Q&A clarification 2026-06-05.
         Orchestrator is now a standalone user-facing application, not a
         daemon-with-dashboard. Removed MCP-as-spine assumption; MCP is one
         protocol of many in a per-app adapter layer. Removed v1/secondary
         service tiering; all 12 adapters first-class in v1. Added four-mode
         selection capture (browser, drag-drop, context menu, palette).
         Removed autopilot-from-day-one assumption; autopilot infrastructure
         built but gated off, sandbox testing only in v1. Added strict
         approval defaults. Added 24/7 operation, notification-file-feed
         spine with tray + email subscribers. Added cross-OS portability
         from the bones, Windows first to ship. Replaced phased construction
         with 24 capability targets ordered into 7 leverage-prioritized
         waves. Added explicit predecessor retirement of v1/v2/v3.
         Root cause of reframe: v3.0 conflated observability with
         interface, assumed Claude Desktop as primary surface, treated MCP
         as universal spine, and tiered services arbitrarily. User
         correction: "you need to get the fuck over yourself and create a
         separate interaction layer beyond the app... MCPs are built for
         the apps, an MCP for Claude Desktop does not work for Codex, for
         Copilot, Ollama, Hermes... You cannot treat any of this as
         secondary, it's one unified system... We sure are shit not ready
         for [autopilot] right now. We need to build the infrastructure to
         be able to work anywhere on anything that I use a selection system
         to identify as what is needed to be worked on."
  3.0 -- SUPERSEDED. Process orchestration daemon with monitoring UI.
         Architecturally wrong on user surface, MCP role, service tiering,
         and autopilot readiness.
  2.0 -- SUPERSEDED. Client-app router embedded in Claude Desktop.
  1.0 -- SUPERSEDED. App wrapper.
```

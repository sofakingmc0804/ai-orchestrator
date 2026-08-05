# Hermes Harness Architecture

**Status:** Proposed, pending owner acceptance
**Date:** 2026-08-04
**Machine:** DESKTOP-LOOCRQ2 (single machine; MSI explicitly out of scope by owner decision)
**Authority:** extends `dev/ai-orchestrator/`. Creates no parallel routing, metering, or budget system.

---

## 0. What this document is

A build plan for turning Hermes Agent from an installed tool into a governed personal agent
harness. It is scoped to what was verified on this machine on 2026-08-04, and it names what was
not verified.

Two prior claims are corrected here before anything is built on them.

**The routing brain does not need to be a service.** Earlier design work assumed the FastAPI app
on port 8765 was the brain. It has emitted no supervisor receipt since 2026-07-29T02:04Z despite
a 60-second watchdog loop that writes an `already_running` receipt every 15 minutes when alive.
That is six days of silence. Everything routed through its HTTP endpoints, its in-app scheduler,
its budget probes, and its backlog discovery has been dormant that whole time. This is the third
always-on component here to fail quietly, after `.ai-resource-governor` (retired 2026-06-17 after
an OAuth race burned a single-use Nous token) and `sync-contract-plugin` (1,569 consecutive
failures). The architecture below removes always-on infrastructure from the critical path.

**The epistemic enforcement hook cannot enforce anything.** `config.yaml` lines 428 to 434 wire
`enforcement/epistemic/hooks/hermes-stop.ps1` into `post_llm_call` and `subagent_stop`. That
script dot-sources `enforcement/epistemic/engine.ps1`, whose own header states it is retired and
which returns `allow` unconditionally while appending one JSONL audit line. Reading `config.yaml`
alone, enforcement appears live. It is an audit logger with a 45-second timeout on every LLM call.

---

## 1. Owner constraints (non-negotiable, encoded as code in Phase 1)

| Constraint | Source | How it becomes runtime, not prose |
|---|---|---|
| Never route to Anthropic API or any metered class | `governance_policy.json` `disabled_billing_classes`, `worker_routing.py:38` `CONTRACT_BLOCKLIST`, `auth.json` `suppressed_sources` | `llm_request` middleware rejects before dispatch |
| No active desktop takeover (pointer, keyboard, foreground window) | Owner directive 2026-08-04; `~/.claude/CLAUDE.md` | `tool_request` middleware blocks `computer_use` input verbs; `cua-driver-serve` task retired |
| No external send, no cold outreach | `~/.claude/CLAUDE.md` | `tool_request` middleware blocks send verbs on non-allowlisted recipients |
| Every question to owner uses the clarify tool, never inline chat | `memories/USER.md` | `pre_llm_call` hook injects the constraint; `tool_request` middleware flags inline-question patterns |
| No em-dashes in any copy written for Matt | `~/.claude/CLAUDE.md` (permanent, all profiles) | `transform_llm_output` hook rewrites in the turn finalizer (see the streaming caveat in Section 6) |
| Universal, not built for one domain | Owner decision 2026-08-04 | Verticals are kanban boards and skills, never plugin code |
| Single machine; no automation silently depending on MSI | Owner decision 2026-08-04 | No cross-machine calls anywhere in the design |
| No visible-window background jobs | `~/.claude/CLAUDE.md` | All self-scheduling goes through `no-console-task-launcher.pyw` or `hidden-stdio-launcher.exe` |

Granted unattended, per owner decision 2026-08-04: headless browser (Playwright/CDP in its own
context), file create/modify/delete inside owner workspaces, self-registration of scheduled tasks
via the hidden-launcher path, and free routing across flat-rate and free capacity.

---

## 2. Why Hermes is the right substrate

Verified extension surface in `AppData/Local/hermes/hermes-agent/` (Hermes Agent 0.19.1, MIT):

**Eighteen registration points** on `PluginContext` (`hermes_cli/plugins.py`): tools, CLI commands,
slash commands, context engines, image gen, video gen, web search, browser, TTS, transcription,
dashboard auth, secret sources, platforms, Slack actions, auxiliary tasks, hooks, middleware,
skills.

**Twenty-three hook events** (`hermes_cli/plugins.py:135` `VALID_HOOKS`), including `pre_tool_call`,
which can block, and `transform_llm_output`, which can rewrite.

**Four middleware phases** (`hermes_cli/middleware.py:29` `VALID_MIDDLEWARE`): `tool_request`,
`tool_execution`, `llm_request`, `llm_execution`. The phases are not interchangeable and their
real capabilities were measured, not assumed:

| Phase | Can do | Cannot do |
|---|---|---|
| `llm_request` | rewrite `api_kwargs`, including **model** | change **provider**; `base_url` and credentials come from `self._client_kwargs` at `run_agent.py:4705`, which middleware never sees |
| `llm_execution` | wrap `next_call` and substitute a different call entirely | nothing relevant; this is the phase with real redirect power |
| `tool_request` | rewrite tool **arguments** only (`middleware.py:165` honors `result["args"]`) | **block**; no deny shape exists in the contract |
| `tool_execution` | wrap the call and decline to invoke `next_call`, which is a real block | nothing relevant |

**Both middleware and hooks fail open.** `plugins.py:1965-1976`, `middleware.py:303-314`, and
`conversation_loop.py:2114` all swallow callback exceptions and continue, and
`_run_execution_chain` explicitly falls through to the next link after a raise. A governor that
refuses by raising is silently bypassed. Every refusal in this design is a **return value**, never
an exception.

**Four programmatic surfaces**: ACP JSON-RPC over stdio (`acp_adapter/`), TUI gateway JSON-RPC
with roughly forty methods (`tui_gateway/`), an OpenAI-compatible API server
(`gateway/platforms/api_server.py`), and a 141-route FastAPI dashboard (`hermes_cli/web_server.py`).
Hermes can also run as an MCP server two independent ways.

**Existing usage volume**: 262 sessions, 17,696 messages, 493M input tokens over 57 days,
124 of those sessions cron-driven. This is a load-bearing install, not a trial.

The decisive point is that Matt's own operating doctrine already reached this conclusion.
`memories/USER.md` states: *"Written skill enforcement is insufficient; runtime automation
(desktop plugins, cron) is the only real enforcement."* `skills/translation-engine/SKILL.md`
states: *"Written rules are NOT sufficient. LLMs ignore them under context pressure."* The harness
is the completion of that thesis. Everything currently living in prose (CLAUDE.md, `.claude/rules/`,
`epistemic-rules.md`, `artifact-rules.md`, `efficiency_policy.json`, the skills) moves to
Hermes registration points where it executes instead of being read and forgotten.

---

## 3. Architecture

Five layers. Each is independently useful and independently reversible.

```
  Layer 4   OBSERVE     dashboard plugin tab · receipts · ladder · cost · quota
  Layer 3   REACH       Hermes Desktop panes · WhatsApp Cloud · Cowork handoff seam
  Layer 2   WORK        kanban work graph · contract intake · git ledger
  Layer 1   GOVERN      solon-governor plugin: middleware · hooks · secrets
  Layer 0   REPAIR      make the foundations true before stacking on them
```

### Layer 0: Repair

Nothing below is built until these are true, because each one currently makes the system lie
about its own state.

**Web search is dead for every agent and every cron job.** `web.backend: firecrawl` in
`config.yaml:54`, but `FIRECRAWL_API_KEY` is commented out in `.env`. Roughly 85 of the last
3,000 lines of `errors.log` are this single failure. The keyless replacement is
`plugins/web/ddgs`, which needs `web.backend: ddgs` **plus** `pip install ddgs` into Hermes's own
3.11 venv, because the package is not currently present there (`provider.py:311-315` returns a
"not installed" error). `plugins/web/brave_free` is not keyless despite the name: it requires
`BRAVE_SEARCH_API_KEY` and reports unavailable without it (`provider.py:51-54`). Since
`search_backend` and `extract_backend` are both empty in the live config, the one backend key
covers both capabilities.

**Two cron jobs fail on a path-doubling bug.** Both `sync-contract-plugin` and `quota-monitor`
put a directory prefix in the `script` field, which is then resolved relative to
`$HERMES_HOME/scripts/`, producing `...\hermes\scripts\scripts\quota_probe.py`. The first has
failed 1,569 times at one attempt per minute and has consumed 992 of the 1,000 rows in the
rolling `cron/executions.db`, destroying roughly two months of GovCon run history every day.
Fixing it recovers that retention. It also means the desktop plugin's contract data has been
frozen since 2026-08-03, so the enforcement mechanism `translation-engine` depends on is dead.

**The auxiliary model chain is pinned to retired models.** `config.yaml` sets
`auxiliary.compression.model: qwen3-next:80b` and `auxiliary.skills_hub/mcp/title_generation:
ministral-3:8b`. Both return `was retired at ...` from Ollama Cloud. Context summarization is
silently failing. The fix is not a new hardcode, which would violate the no-hard-coded-temporal-state
rule. It is a startup reconciliation against `ollama_cloud_models_cache.json`, which already
refreshes on boot and currently lists 20 live models.

**`cua-driver-serve` is armed against owner policy.** An elevated scheduled task
(`RunLevel=Highest`, Session 1, named pipe `\\.\pipe\cua-driver`, autostart at interactive logon)
exists solely to give Hermes foreground pointer and keyboard input. The owner banned active
desktop takeover on 2026-08-04. Retire the task and remove the driver scripts from
`skills/translation-engine/scripts/`.

**`plugins.enabled` is malformed.** `config.yaml:582` holds `'[''translation-engine'']'`, a YAML
string containing a Python-repr list rather than a YAML sequence. It happens to work. It should
be a real list before more plugins are added to it.

**The enforcement hook is decided, not left ambiguous.** Either the `hermes-stop.ps1` wiring is
removed, or it is replaced by the real Python hook in Layer 1. Leaving a hook that reads as
enforcement and behaves as a logger is the failure mode this whole harness exists to end.

### Layer 1: The `solon-governor` plugin

One Hermes plugin. This is the core of the harness.

Location: `AppData/Local/hermes/plugins/solon-governor/` with `plugin.yaml`, `__init__.py`
exporting `register(ctx)`, and a `dashboard/` subfolder for Layer 4.

| Registration | Phase | Responsibility |
|---|---|---|
| `register_middleware("llm_execution")` | wraps every model call | **The ladder.** Rank in-process, then substitute the call against the chosen provider. This phase is used rather than `llm_request` because provider and `base_url` are not reachable from `api_kwargs` |
| `register_middleware("llm_request")` | before every model call | Model-only adjustments within the already-chosen provider, plus attaching the decision id to the receipt |
| `register_hook("pre_tool_call")` | blocking gate | **The safety envelope.** Refuse pointer input, external send, metered providers, and out-of-workspace writes by returning `{"action":"block","message":"..."}`. This is a hook, not middleware, because `tool_request` middleware has no deny shape |
| `register_middleware("tool_execution")` | wraps every tool call | Receipts (inputs hash, outputs hash, duration, exit state), plus second-line refusal by declining to invoke `next_call` |
| `register_middleware("tool_request")` | before every tool call | Argument normalization only, which is the limit of what this phase can do |
| `register_hook("transform_llm_output")` | rewrite | Strip em-dashes and any other absolute copy rule before delivery |
| `register_hook("kanban_task_*")` | observer | Work-graph state transitions into the ledger |
| `register_secret_source` | credentials | One source of truth instead of scattered `.env` keys |
| `register_cli_command` | operator | `hermes governor status / ladder / why / receipts` |

Every one of these refuses by return value. None raises. See the fail-open finding in Section 2.

**The critical improvement over today.** The current `scripts/hermes-router.ps1` shim spawns
`orch` as a subprocess and only injects a provider for jobs that were explicitly classified as
orchestrator jobs. Manual chat bypasses governance entirely. Middleware sees **every** call,
including manual chat, subagents, cron jobs, the auxiliary model chain, and compression.
Governance stops being opt-in.

**Feasibility is verified by execution, not inspection.** An earlier draft of this plan proposed
converting `orchestrator/models.py` off pydantic to make the routing chain importable under
Hermes's Python 3.11. That was tested and the plan was wrong in a way worth recording, because
the same mistake is easy to repeat:

- Hermes's active venv (`hermes-agent/venv`, uv-managed cpython-3.11) **already contains
  pydantic 2.13.4 and pydantic_core 2.46.4**, and Hermes pins `pydantic==2.13.4` as a hard
  dependency in its own `pyproject.toml`. `orchestrator/models.py` imports unmodified there today.
  The conversion buys nothing.
- The conversion would also have broken things. `brain.py:73` calls
  `decision.model_dump(mode="json")` on the object the governor would consume, and
  `dispatcher.py:98` uses `Selection.model_validate`. Further pydantic-only calls sit in
  `state/store.py`, `notifications/spine.py`, `ui/server.py`, and `cli/main.py`.
- A naive `@dataclass` conversion fails immediately (`TypeError: non-default argument 'severity'
  follows default argument`, because `Notification` orders a defaulted field first), silently
  drops the `Field(ge=…, le=…)` constraints at lines 68, 69, and 75, and loses JSON serialization
  of `datetime` and `Path` that `model_dump(mode="json")` provides.
- The proof gate proposed for it ("import test passes") would have gone green while the
  dispatcher, store, and spine broke at runtime.

**The actual integration constraint is different and smaller.** `ai-orchestrator/pyproject.toml`
declares `requires-python = ">=3.13"`, so the package cannot be pip-installed into Hermes's 3.11
venv. The plugin injects the orchestrator repo path into `sys.path` at `register(ctx)` time and
imports the routing chain directly. Confirmed importable and executable under 3.11: `models.py`,
`routing/engine.py`, `routing/worker_routing.py` (700 lines), `provider_aliases.py`,
`surface_adapters.py`, and `hermes/capacity_lanes.py` (98 lines, already pure stdlib). `route_intent()`
was run end to end and returned a `RoutingDecision`.

**The PYTHONPATH hazard runs the other direction and still exists.** Hermes's 3.11 `pydantic_core`
binary poisons the orchestrator's 3.13 venv, which is why the `orch` wrapper clears `PYTHONPATH`.
That hazard is unchanged for CLI use. The plugin avoids it by never crossing interpreters.

**`ai-orchestrator` keeps its authority.** It remains the single canonical definition of the
roster, the policy, the ladder, and the receipt schema. What changes is that the *decision* runs
in-process instead of in a service that has been dead for six days. The FastAPI app is demoted to
an optional read-only viewer that nothing depends on. This preserves the "one generator, one
source" rule while removing the availability dependency, and it is the only topology that stays
correct if a second machine is ever added, since each machine would be a complete self-sufficient
peer with nothing over the wire.

### Layer 2: The universal work substrate

The owner decision was explicit: the harness must be universal and not built for any single
domain. The mechanism for that already exists and is completely unused.

`kanban.db` has seven tables (`tasks`, `task_runs`, `task_events`, `task_comments`,
`task_attachments`, `task_links`, `kanban_notify_subs`) and **zero rows in all of them**. The
schema was initialized 2026-07-29 and never touched. Behind it sits real machinery: a dispatcher
that runs inside the gateway on a 60-second tick (`kanban.dispatch_in_gateway: true`), an LLM
decomposer that turns one task into a child graph (`hermes_cli/kanban_decompose.py`), a swarm
mode with parallel workers plus verifier plus synthesizer (`kanban_swarm.py`), per-board isolated
databases and workspaces, worker subprocesses with per-task model and skill pinning, and three
lifecycle hooks. Two skills already ship for it (`devops/kanban-orchestrator`,
`devops/kanban-worker`).

This is the domain-agnostic work spine, and adopting it is what makes the harness universal:
**verticals become boards, not code.** GovCon, Solon operations, Old Gregg compliance, and
machine self-maintenance are four boards on one engine. Adding a fifth domain later requires a
board and a skill, never a change to the harness.

On top of it sits the work contract already designed in `skills/translation-engine/SKILL.md`
and proven over 30-plus GovCon runs: intake, clarify-tool interview, machine-readable contract
with satisfied and open requirements, approval gate, routed background dispatch, git ledger,
translation back to non-technical language. Layer 2 moves that loop from a skill the model may
ignore under context pressure into kanban state transitions the engine enforces, with the
governor's `kanban_task_*` hooks writing a receipt at every transition.

### Layer 3: Reach

**Hermes Desktop** is the entry platform by owner decision. The current
`desktop-plugins/translation-engine/plugin.js` is a working reference implementation but its data
is a hardcoded `CONTRACT` constant patched by the dead cron job, and it never calls its own Python
backend at `plugins/translation-engine/dashboard/plugin_api.py`, which is written, manifested,
enabled, and unreachable. Its replacement is a governor pane set that reads live through
`ctx.rest()`: the work graph, why-this-model for the last decision, the receipt tail, the approval
queue, and current cost and quota. Contribution areas available and documented in
`skills/hermes-desktop-plugins/SKILL.md`: `panes`, `statusBar.left|right`, palette commands,
keybinds, routes, sidebar nav, and title bar. Plugins hot-reload from disk within seconds, so
iteration is fast.

Note the SDK's own warning, which matters here: `ctx.socket` is a no-op on OAuth remotes, so every
pane keeps a polling fallback. The 21 WebSocket errors in `errors.log` confirm this is not
theoretical.

**WhatsApp via Meta Cloud API**, per owner decision. `gateway/platforms/whatsapp_cloud.py` is the
official Business Platform adapter: Graph API outbound, webhook server with verify-token
handshake, X-Hub-Signature-256 HMAC verification with constant-time comparison, wamid replay
protection, media and voice-note support with ffmpeg opus conversion, and a 24-hour conversation
window with template fallback. It requires `WHATSAPP_CLOUD_PHONE_NUMBER_ID` and
`WHATSAPP_CLOUD_ACCESS_TOKEN` (System User permanent token), plus `WHATSAPP_CLOUD_APP_SECRET`
for HMAC and `WHATSAPP_CLOUD_VERIFY_TOKEN` for the handshake. Default webhook port 8090, path
`/whatsapp/webhook`.

This has one real prerequisite worth naming now rather than discovering later: Meta requires a
publicly reachable HTTPS webhook. That means a tunnel or a reverse proxy terminating TLS. It does
not require inbound firewall changes if a tunnel is used, and it does not create a cross-machine
dependency. The Baileys bridge alternative was not chosen and is not built.

Once paired, WhatsApp becomes the delivery target for cron results (all six jobs currently
deliver `local`, meaning results die in a log), for approval-queue notifications, and for ad-hoc
requests away from the desk.

**The Cowork handoff seam.** Claude is subscription-covered inside Cowork and Claude Code, and
routing it through Hermes would bill API on top. Anthropic OAuth is also barred by ToS from
third-party tools, and Hermes has silently pooled a `claude_code` credential that must stay
unused. So the two never call each other. Instead they exchange work through a file-based queue
with single-writer semantics: Claude writes a work item, Hermes picks it up and executes on
flat-rate capacity, Hermes writes a result, Claude reads it. `dev/collab/collab.py` and the
`claude-ecosystem` handoff and queue commands already exist as the starting point. This is also
the only pattern that would extend to a second machine without violating the multi-writer
prohibition.

### Layer 4: Observe

A dashboard plugin tab, following the shipped `plugins/kanban/dashboard/` pattern exactly
(`manifest.json` with a `tab` path and position, `plugin_api.py`, built `dist/index.js`). It is
discovered by `_discover_dashboard_plugins()` in `hermes_cli/web_server.py:16295` and mounted at
startup, so it needs no separate process. Content: the live ladder and why each decision was made,
the receipt stream, cost and quota by provider, cron health including the failure counts that are
currently invisible in logs, and skill usage from `.usage.json`.

One deliberate inclusion: `sync-contract-plugin`'s 992 daily failures appear **only** in
`cron/executions.db`, not in `errors.log`. A log-only review misses the single most frequent
failure in the system. The observability layer reads both.

---

## 4. What this produces that does not exist today

Governance covers manual chat, not just explicitly classified jobs. Enforcement can actually
refuse rather than log. Every model decision carries a stored reason answerable by
`hermes governor why`. The work graph is a real engine with decomposition and parallel workers
instead of a markdown file. Results reach a phone instead of a local log. Nothing depends on a
service staying up. New domains are boards, not code. And the copy rules that currently depend on
a model remembering them become a rewrite pass that cannot be forgotten.

---

## 5. Sequence

| Phase | Work | Proof of completion |
|---|---|---|
| 0 | Repair: `web.backend: ddgs` plus `pip install ddgs` into Hermes's venv, cron paths, aux model reconciliation, retire `cua-driver-serve`, fix `plugins.enabled`, decide the enforcement hook | `errors.log` clean of Firecrawl and retirement errors; `executions.db` retention recovering; task gone from Task Scheduler |
| 1a | `solon-governor` skeleton: `sys.path` injection, roster load, ladder ranked in-process, receipts | `hermes governor ladder` returns a ranked ladder with reasons, inside Hermes's interpreter |
| 1b | `llm_execution` middleware carrying the ladder; `llm_request` for model-only adjustment | A manual chat turn routes to a governed provider and stores a receipt with a reason |
| 1c | `pre_tool_call` blocking gate and `transform_llm_output`; remove the no-op PowerShell wiring | A pointer-input attempt is refused by return value; a raised exception inside the governor is proven not to bypass the gate |
| 2 | Adopt kanban as the work substrate; port the translation-engine contract loop onto it; first board | A task decomposes, dispatches, and lands in the ledger with receipts at each transition |
| 3a | Desktop governor panes replacing the frozen plugin | Panes read live via `ctx.rest()`, no hardcoded constants |
| 3b | WhatsApp Cloud pairing and cron delivery retarget | A cron result and an approval request both arrive on the phone |
| 3c | Cowork handoff seam | A work item crosses from Claude to Hermes and a result returns |
| 4 | Dashboard tab | Ladder, receipts, cost, and cron failures visible in one place |

Phases 0 and 1 are the load-bearing ones. Everything after is additive and can be reordered.

---

## 6. Risks, stated plainly

**Middleware and hooks fail open, by design, in Hermes.** This is the most important risk in the
build. `plugins.py:1965-1976`, `middleware.py:303-314`, and `conversation_loop.py:2114` all catch
and discard callback exceptions and continue. A governor that refuses by raising is not a
governor. Every gate must refuse by return value, and Phase 1c's proof gate is specifically a test
that an exception thrown inside the governor does **not** open the gate.

**The copy-rule rewrite is conditional on streaming staying off.** `transform_llm_output` fires
once per turn in `agent/turn_finalizer.py:529-543`, after the tool loop, and only when
`final_response` exists and the turn was not interrupted. With streaming on, the original text has
already reached the user; gateway (`run.py:24589`) and ACP (`server.py:1942`) then re-send the
corrected text, so the user sees both versions, and `cli.py` does not consume
`response_transformed` at all, so terminal chat keeps the uncorrected original on screen. This
install currently has `display.streaming: false` and `streaming.enabled: false` in `config.yaml`,
which is why the mechanism works here. **Turning streaming on breaks it.** If streaming is ever
wanted, the copy rule has to move into the system prompt plus a delivery-side filter instead.

WhatsApp Cloud needs a public HTTPS endpoint and there is no polling fallback: inbound is an
aiohttp server (`whatsapp_cloud.py:447-458`). `WHATSAPP_CLOUD_APP_SECRET` is warn-not-fail at
startup but is required for inbound delivery (`:1478`). Meta business verification timing is
outside this machine's control.

`state.sqlite` in `.runtime/orchestrator/` is 2.6 GB and could not be queried from the analysis
sandbox because a trigger uses the `->` JSON operator that SQLite 3.37.2 cannot parse. Every row
count cited in prior handoffs comes from documents, not from the database. **UNVERIFIED.**

The `ai-orchestrator` working tree has 166 modified and 15 untracked files uncommitted on `main`,
including the entire 2026-08-04 Hermes capacity subsystem. Building on top of uncommitted work
risks losing it.

Provider capacity as of 2026-08-04: Nous reports access depleted, OpenCode Zen is visible but
billing-blocked, OmniRoute is held at `unknown_cost`, and the live roster carries **1**
`local_resource` worker against 17 in the source roster, despite `governance_policy.json` putting
`local_resource` first in `fallback_order`. Whether that is intentional pruning or a
reconciliation bug is **UNVERIFIED** and matters, because it is the floor the whole ladder falls
back to.

**The orchestrator is not dead, its supervisor is.** The narrow claim in Section 0 is that no
supervisor receipt exists after 2026-07-29T02:04Z, and that holds against all 22,981 receipts. But
`.runtime/orchestrator/state.sqlite` has an mtime of 2026-08-04T23:31Z and the live roster was
written 2026-08-04T18:38Z, so orchestrator code demonstrably ran today via the `orch` CLI. What is
dead is the watchdog, the FastAPI service, the in-app scheduler, the budget probes, and backlog
discovery. Do not read Section 0 as "the orchestrator has not run in six days."

Everything in this document describes DESKTOP-LOOCRQ2. No MSI evidence was gathered, by owner
decision.

---

## 6a. Verification record

This plan was checked adversarially against source before acceptance. Ten load-bearing claims were
attacked. Six were corrected in place; the corrections are already reflected above.

| Claim | Verdict |
|---|---|
| `pre_tool_call` blocks; `transform_llm_output` rewrites | CONFIRMED (`plugins.py:2130-2192`, `turn_finalizer.py:529-543`) |
| Kanban unused and capable | CONFIRMED (0 rows; dispatcher at `kanban_watchers.py:947`, mixed into `run.py:2210`) |
| WhatsApp Cloud env vars and webhook requirement | CONFIRMED, no polling mode exists |
| Dashboard plugin discovery and manifest shape | CONFIRMED, `web_server.py:16295` exact |
| Cron `script` resolves against one root only | CONFIRMED (`cron/scheduler.py:2233-2251`), diagnosis correct |
| Supervisor silent since 2026-07-29 | CONFIRMED, with the over-read caveat above |
| `engine.ps1` returns allow unconditionally | CONFIRMED, single unbranched return at line 32 |
| Pydantic conversion needed for 3.11 import | **REFUTED.** Hermes ships pydantic 2.13.4; conversion unnecessary and would break four modules. Phase removed |
| `llm_request` middleware can rewrite provider | **REFUTED.** Model only. Ladder moved to `llm_execution` |
| `tool_request` middleware can block | **REFUTED.** Args only. Blocking moved to `pre_tool_call` and `tool_execution` |
| Web backend swap is one line, zero cost | **REFUTED.** `ddgs` needs a pip install; `brave_free` needs a key |
| Hook count 25, kanban tables 8 | **REFUTED.** 23 and 7 |

---

## 7. Predecessor retirement record (GATE 6 / artifact-rules)

**Retired in this action:**

`docs/hermes/HERMES_MODEL_ROSTER_2026-06-09.md` and `docs/hermes/HERMES_WALKTHROUGH_2026-06-09.md`
moved to `docs/archive/`. Both describe a 2026-06-09 roster state superseded by
`HERMES_CAPACITY_IMPLEMENTATION_2026-08-04.md` and by the live roster at
`.runtime/orchestrator/worker_roster_v2.json` (68 workers, 2026-08-04T18:38:41Z). The June roster
predates OpenRouter, Nous, OpenCode Zen, LM Studio, and OmniRoute entirely.

**Retained with reason:**

`FREE_TIER_STACKING_PLAN_EVALUATION_2026-08-04.md` retained as the evidence record for provider
and OAuth state. Its Rev 2 build sequence is superseded by this document and by its own Rev 3
runbook; the evidence sections are not.

`HERMES_CAPACITY_IMPLEMENTATION_2026-08-04.md` retained. It documents completed lane-translation
work that this architecture consumes as an input.

`NOUS_AUTH_REPAIR_2026-06-16.md` retained. It is the incident record for the `.ai-resource-governor`
retirement and remains the reason the shared-runtime-state pattern is prohibited.

**Scheduled for retirement in Phase 0, named here so it is not forgotten:** the `cua-driver-serve`
scheduled task and the `cua_driver_mcp.py` / `cua_driver_serve.py` scripts under
`skills/translation-engine/scripts/`, superseded by the owner's 2026-08-04 ban on active desktop
takeover.

Orphan retirement: 2 retired, 3 justified-retained, 3 scheduled.

# AI Operating Layer
# Process Orchestration Specification
## Version: 3.0
## Date: 2026-06-04
## Machine: Dell Inspiron 7706 2n1 (DESKTOP-LOOCRQ2)

---

# WHAT THIS IS

A persistent orchestration daemon that sits between Windows and all AI applications on this Dell. It launches, monitors, reads, coordinates, and routes work across every AI process. The apps are processes it manages. The UIs reflect what it is doing. It is not an app that wraps other apps. It is the layer that operates them.

# WHAT THIS IS NOT

- Not a new chat interface (Claude Desktop and Codex Desktop already exist)
- Not an API proxy (no API keys exist on this machine; all cloud access is subscription-included through client apps)
- Not a Claude skill or CLAUDE.md config (that makes Claude the kernel; Claude is just another managed process)
- Not a React dashboard sitting alongside the apps (that's a peer, not a controller)

---

# DISCOVERED MACHINE STATE (Runtime-verified 2026-06-04)

## Process Groups

| Service | Process Name(s) | Process Count | Source Path | Status |
|---------|----------------|---------------|------------|--------|
| Claude Desktop | claude.exe | ~18 | C:\Program Files\WindowsApps\Claude_1.11187.1.0_x64_... | Running |
| Claude Code CLI | claude.exe | 1-2 | C:\Users\Couch\.local\bin\claude.exe | Running |
| Claude Code (embedded) | claude.exe | 1 | %APPDATA%\Claude\claude-code\2.1.161\claude.exe | Running |
| Codex Desktop | Codex.exe, codex.exe | ~10 | C:\Program Files\WindowsApps\OpenAI.Codex_26.602.3474.0_x64_... | Running |
| Codex CLI | codex.exe | 2 | %LOCALAPPDATA%\OpenAI\Codex\bin\...\codex.exe | Running |
| Codex node_repl | node_repl.exe | 2 | %LOCALAPPDATA%\OpenAI\Codex\bin\...\node_repl.exe | Running |
| Ollama | ollama.exe, ollama app.exe | 2 | %LOCALAPPDATA%\Programs\Ollama\ | Running |
| Cowork VM | (service) | 1 | CoworkVMService Windows Service | Running |
| Node.js (MCP servers) | node.exe | 6 | C:\Program Files\nodejs\node.exe | Running |
| Python (extensions) | python.exe | 5 | C:\Python313\ and Claude Extensions | Running |
| Hermes Agent | (none) | 0 | %APPDATA%\Python\Python313\Scripts\hermes.exe | Installed, not running |
| OpenClaw | (none) | 0 | %APPDATA%\npm\openclaw | Installed, not running |
| LM Studio | (none) | 0 | Installed, not started | Installed, not running |

## Log Files (Ingestible Output Streams)

| Service | Log Path | Size | Content |
|---------|----------|------|---------|
| Claude Desktop | %APPDATA%\Claude\logs\main.log | 7.8 MB | App events, session lifecycle, errors |
| Claude MCP: Desktop Commander | %APPDATA%\Claude\logs\mcp-server-Desktop Commander.log | 97 KB | MCP tool calls and responses |
| Claude MCP: PDF Tools | %APPDATA%\Claude\logs\mcp-server-PDF Tools....log | 45 KB | PDF tool calls |
| Claude Cowork VM | %APPDATA%\Claude\logs\cowork_vm_node.log | 1.1 MB | VM lifecycle, session activity |
| Claude SSH | %APPDATA%\Claude\logs\ssh.log | 55 KB | SSH tunnel events |
| Claude Chrome Bridge | %LOCALAPPDATA%\Claude\logs\chrome-native-host.log | 647 KB | Chrome extension communication |
| Codex | %USERPROFILE%\.codex\*.log | ~20-80 KB/day | Daily activity logs |
| Ollama | API at localhost:11434 (no log files found) | N/A | Query /api/tags, /api/ps for state |

## Auth and Billing State (Discovered)

| Provider | Auth | Subscription | API Key | Billing Class |
|----------|------|-------------|---------|---------------|
| Anthropic/Claude | claude.ai OAuth (Max) | subscriptionType: "max" | NONE | subscription_included |
| OpenAI/Codex | OpenAI OAuth | ChatGPT subscription | NONE | subscription_included |
| GitHub/Copilot | Keyring (sofakingmc0804) | Copilot subscription | NONE | subscription_included |
| Ollama | Local (none needed) | N/A | NONE | local_free |
| Google/Gemini | NOT CONFIGURED | Unknown | NONE | undetermined |

## Existing Coordination Infrastructure

| Mechanism | Status | What It Provides |
|-----------|--------|-----------------|
| Claude Desktop MCP config | mcpServers: {} (EMPTY) | Would provide tool connections to Claude Desktop |
| Codex MCP servers | node_repl configured | Provides REPL and browser control to Codex |
| Claude scheduled tasks | scheduled-tasks.json: 27 bytes (empty/minimal) | Would provide autonomous background execution |
| Codex trusted projects | 20 projects configured | Project registry with trust levels |
| CoworkVMService | Running as Windows Service | Provides sandboxed execution environment |

---

# ARCHITECTURE

## Layer Model

```
┌──────────────────────────────────────────────────┐
│                  MATT (User)                      │
│         Sees UIs, monitors activity,              │
│         dispatches high-level intent              │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│           AI OPERATING LAYER (this spec)          │
│                                                   │
│  Persistent daemon. Runs as Windows service or    │
│  startup process. Sits between Windows and all    │
│  AI apps. Manages process lifecycle, ingests      │
│  output streams, routes work, maintains state.    │
│                                                   │
│  ┌─────────────────────────────────────────────┐  │
│  │ PROCESS MANAGER                             │  │
│  │ Starts, stops, restarts, monitors all AI    │  │
│  │ processes. Knows process groups, PIDs,      │  │
│  │ memory, uptime. Detects crashes.            │  │
│  └─────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────┐  │
│  │ I/O INGESTION                               │  │
│  │ Tails log files. Polls status APIs. Probes  │  │
│  │ CLI auth commands. Captures stdout of       │  │
│  │ processes it launches. Parses structured    │  │
│  │ output (JSON logs, API responses).          │  │
│  └─────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────┐  │
│  │ ROUTING ENGINE                              │  │
│  │ Accepts work intent (task type, project,    │  │
│  │ requirements). Selects which managed        │  │
│  │ process group handles it. Dispatches via    │  │
│  │ CLI invocation, API call, or stdin.         │  │
│  │ Never creates API keys. Never uses metered  │  │
│  │ endpoints. Routes to subscription client    │  │
│  │ apps for cloud work, Ollama for local.      │  │
│  └─────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────┐  │
│  │ STATE STORE (SQLite)                        │  │
│  │ Process registry, receipts, health records, │  │
│  │ project inventory, routing decisions,       │  │
│  │ repair queue, discovery log.                │  │
│  └─────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────┐  │
│  │ MONITORING SURFACE                          │  │
│  │ Web UI served by the daemon. Reflects       │  │
│  │ process state, ingested output, routing     │  │
│  │ decisions. Matt monitors here. Not a        │  │
│  │ separate app; it's the daemon's own view    │  │
│  │ into what it's managing.                    │  │
│  └─────────────────────────────────────────────┘  │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│              MANAGED PROCESSES                    │
│                                                   │
│  Claude Desktop (18 procs, subscription)          │
│  Claude Code CLI (1-2 procs, subscription)        │
│  Codex Desktop (10 procs, subscription)           │
│  Codex CLI (2 procs, subscription)                │
│  Ollama (2 procs, local free)                     │
│  Hermes Agent (0 procs, launch on demand)         │
│  OpenClaw Gateway (0 procs, launch on demand)     │
│  Copilot (via VS Code, subscription)              │
│  Gemini CLI (not configured yet)                  │
│  LM Studio (installed, launch on demand)          │
│  Node.js MCP servers (6 procs, managed by apps)   │
│  Python extensions (5 procs, managed by apps)     │
│                                                   │
└──────────────────────────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│                 WINDOWS OS                        │
│          Process scheduler, filesystem,           │
│          network stack, GPU driver                │
└──────────────────────────────────────────────────┘
```

## Daemon Runtime

The daemon is a single Python process (`ai-opsys.py` or installed as a package) that:

1. Runs at Windows startup (via Task Scheduler or as a Windows service)
2. Spawns worker threads for:
   - Process monitor (psutil-based, polls every 5 seconds)
   - Log tailer (watches log files for new lines, parses them)
   - API poller (hits Ollama /api/tags, future OpenClaw /health, etc.)
   - CLI prober (runs `claude auth status`, `gh auth status` on schedule)
   - Web server (serves the monitoring surface)
   - WebSocket broadcaster (pushes events to the monitoring surface)
3. Maintains SQLite state that persists across restarts
4. Can launch managed processes (Hermes, OpenClaw gateway) as child processes with captured stdout/stderr
5. Can dispatch work to existing processes via their CLIs

## How the Daemon Reads Each App

| App | Ingestion Method | What It Learns |
|-----|-----------------|---------------|
| Claude Desktop | Tail `main.log` + parse JSON entries | Session starts/ends, model calls, errors, scheduled task runs |
| Claude MCP servers | Tail `mcp-server-*.log` | Tool calls to Desktop Commander, PDF Tools, etc. |
| Claude Cowork VM | Tail `cowork_vm_node.log` | VM lifecycle, session activity |
| Claude Chrome | Tail `chrome-native-host.log` | Browser automation events |
| Codex Desktop | Tail daily `*.log` files in `~/.codex/` | Session activity, model calls, plugin invocations |
| Ollama | Poll `localhost:11434/api/tags` and `/api/ps` | Model inventory, currently loaded models, running inferences |
| Hermes | Capture stdout/stderr (daemon-launched) + tail `~/.hermes/logs/` | Agent activity, skill creation, model calls |
| OpenClaw | Capture stdout/stderr (daemon-launched) + poll gateway API | Agent turns, channel messages, model calls |
| Copilot | `gh auth status` + VS Code extension status | Auth state, availability |
| Gemini | `gemini` CLI status (when configured) | Auth state, availability |
| System | psutil process enumeration | CPU/RAM/GPU usage, process health, crash detection |

## How the Daemon Dispatches Work

| Destination | Dispatch Method | When Used |
|-------------|----------------|-----------|
| Ollama (local) | HTTP POST to localhost:11434/v1/chat/completions | Local inference: OCR, embedding, classification, cheap reasoning |
| Claude Code CLI | subprocess: `claude -p "<prompt>" --max-turns 1 --output-format json` | Subscription-included Claude work: complex coding, analysis, writing |
| Codex CLI | subprocess: `codex -p "<prompt>" --json` | Subscription-included OpenAI work: creative writing, alternative reasoning |
| Hermes | subprocess: `hermes run "<task>"` or gateway API (if daemon-launched) | Self-learning agent tasks, persistent skill execution |
| OpenClaw | Gateway API at localhost:18789 | Multi-channel agent tasks, messaging platform work |
| Copilot | `gh copilot suggest` or VS Code extension API | Code completion, suggestions |

## How All AI Services See Each Other

The daemon's SQLite state store is the shared consciousness. Any managed process can query it (via the daemon's API or by reading the SQLite file directly):

- `/status` -- what processes are running, their health, their auth state
- `/models` -- what models are available across all services
- `/activity` -- recent work done by all services
- `/projects` -- all known projects and their routing policies
- `/routing` -- why work was routed where it was

The monitoring surface (web UI) is just one consumer of this shared state. Any AI agent (Hermes, OpenClaw, Claude via MCP) can also consume it if given access.

---

# PHASED IMPLEMENTATION

## Phase 0: Daemon Skeleton + Process Monitor

**Goal:** A Python daemon that starts, discovers all running AI processes, and prints their status. The smallest possible thing that proves the layer exists and can see the apps.

**Tasks:**
- [ ] Create `C:\Users\Couch\.ai-opsys\` directory
- [ ] Write `daemon.py`: single-file Python script using psutil + asyncio
- [ ] Implement process discovery: find all running AI processes by name pattern matching (claude, codex, ollama, hermes, openclaw, node, python)
- [ ] Group processes by service (Claude Desktop = all WindowsApps claude.exe, Codex Desktop = all WindowsApps Codex.exe, etc.)
- [ ] Print process table: service name, PID count, total memory, uptime
- [ ] Implement health check: is Ollama API responding? Is Claude auth valid?
- [ ] Run once, print report, exit

**Acceptance criteria:**
- Running `python daemon.py` prints a table showing Claude Desktop (18 procs), Codex Desktop (10 procs), Ollama (2 procs, API healthy), Hermes (not running), OpenClaw (not running)
- Auth state shown for Claude (Max), Codex (configured), Copilot (logged in)

---

## Phase 1: Log Ingestion + Activity Stream

**Goal:** The daemon tails log files from all AI services and produces a unified, chronological activity stream.

**Entry criteria:** Phase 0 complete.

**Tasks:**
- [ ] Implement log tailer: asyncio file watcher for known log paths
  - `%APPDATA%\Claude\logs\main.log`
  - `%APPDATA%\Claude\logs\mcp-server-*.log`
  - `%APPDATA%\Claude\logs\cowork_vm_node.log`
  - `%LOCALAPPDATA%\Claude\logs\chrome-native-host.log`
  - `%USERPROFILE%\.codex\*.log` (current day's file)
- [ ] Implement log parser: extract structured events from raw log lines
  - Identify: session starts, model calls, tool invocations, errors, completions
  - Normalize to common event format: `{timestamp, service, event_type, detail, model, tokens}`
- [ ] Implement Ollama poller: periodically hit `/api/ps` to see active inferences
- [ ] Implement SQLite receipts table: every parsed event becomes a row
- [ ] Implement unified activity stream: chronological merge of all parsed events
- [ ] Daemon runs continuously (not one-shot), tailing logs in real time
- [ ] Print activity stream to stdout as events arrive

**Acceptance criteria:**
- Start the daemon. Open Claude Desktop, send a message. See the event appear in the daemon's stdout within seconds.
- Open Codex, send a prompt. See the event appear.
- Run `ollama run qwen3.5:0.8b "hello"` in a separate terminal. See it appear.

---

## Phase 2: State Store + Monitoring Surface

**Goal:** SQLite state store persists all discoveries and events. A web interface served by the daemon shows live process state and activity.

**Entry criteria:** Phase 1 complete.

**Tasks:**
- [ ] Implement full SQLite schema: processes, providers, models, projects, receipts, repair_queue, discovery_log
- [ ] Seed providers table from auth discovery (Phase 0 data)
- [ ] Seed models table from Ollama inventory + subscription model lists
- [ ] Seed projects table from Codex config.toml trusted projects
- [ ] Implement web server (FastAPI or plain aiohttp, served by the daemon process)
- [ ] Implement WebSocket event broadcast
- [ ] Build monitoring surface pages:
  - **Process Status**: live view of all AI process groups, PID counts, memory, health indicators
  - **Activity Feed**: real-time event stream from log ingestion, filterable
  - **Model Roster**: all available models across all services, with ratings from roster document
  - **Provider Health**: auth state, billing class, last check per provider
- [ ] Daemon serves the web UI on a configurable port (default 3000)

**Acceptance criteria:**
- Open `http://localhost:3000` in a browser
- See live process status for Claude, Codex, Ollama
- See activity feed updating as events are ingested from logs
- See model roster populated from Ollama + subscription models
- See provider health showing Claude Max, Codex subscription, Copilot logged in

---

## Phase 3: Routing Engine + Work Dispatch

**Goal:** The daemon can accept a work request, decide which managed process should handle it, dispatch it, and log the outcome.

**Entry criteria:** Phase 2 complete.

**Tasks:**
- [ ] Implement task profiles: mapping of task types to required capabilities and preferred services
  - OCR/document intake -> Ollama local (glm-ocr, qwen3-vl)
  - Embedding -> Ollama local (qwen3-embedding, nomic-embed-text)
  - Classification/routing -> Ollama local (qwen2.5:0.5b, qwen3.5:0.8b)
  - General local work -> Ollama local (qwen3.5:9b, qwen3:8b)
  - Local coding -> Ollama local (qwen2.5-coder:7b) then Claude Code
  - Complex coding -> Claude Code CLI (subscription)
  - Creative writing -> Codex CLI (subscription)
  - Heavy reasoning -> Claude Code CLI or Codex CLI
  - Browser automation -> Claude Chrome extension
  - Multi-channel messaging -> OpenClaw
  - Self-learning tasks -> Hermes
- [ ] Implement routing logic:
  1. Determine task type from request
  2. Check if a local model can handle it (always prefer local_free)
  3. If local inadequate: select subscription_included service with best fit
  4. Never select metered or unknown billing class
  5. Check service health before dispatching
  6. Log routing decision with reason
- [ ] Implement dispatch adapters:
  - Ollama: HTTP POST to localhost:11434
  - Claude Code: subprocess `claude -p` with output capture
  - Codex: subprocess `codex -p` with output capture
  - Hermes: subprocess `hermes run` with output capture (launch if not running)
  - OpenClaw: gateway API call (launch gateway if not running)
- [ ] Implement `/dispatch` API endpoint: accepts work request, returns routing decision + result
- [ ] Implement receipt logging: every dispatched call produces a receipt
- [ ] Add dispatch controls to monitoring surface: input field for work requests, routing decision shown, result displayed

**Acceptance criteria:**
- POST to `/dispatch` with `{"task": "classify this text", "text": "..."}` routes to Ollama local model
- POST to `/dispatch` with `{"task": "write complex Python code for ...", "escalation": true}` routes to Claude Code CLI
- Every dispatch produces a receipt in SQLite
- Routing decision includes reason ("selected qwen3.5:0.8b: task=classification, billing=local_free, rating_instruction=5, cheapest competent")

---

## Phase 4: Managed Process Lifecycle

**Goal:** The daemon can launch, stop, and restart managed processes (Hermes, OpenClaw, future services). Crashes are detected and handled.

**Entry criteria:** Phase 0 complete (can run in parallel with Phase 2/3).

**Tasks:**
- [ ] Implement process launcher: start a process with captured stdout/stderr piped to the daemon's log ingestion
- [ ] Implement managed service definitions:
  - Hermes Agent: `hermes run` or `hermes daemon` with Ollama as provider
  - OpenClaw Gateway: `openclaw gateway --port 18789`
  - Ollama (if not already running): `ollama serve`
  - LM Studio (optional): launch app process
- [ ] Implement crash detection: psutil polling detects when a managed process disappears
- [ ] Implement restart policy: configurable per service (always restart, restart N times, alert only)
- [ ] Implement repair queue: when a process fails, log the failure and create a repair entry
- [ ] Add process control to monitoring surface: start/stop buttons per managed service, restart history, crash log

**Acceptance criteria:**
- Daemon starts Hermes as a child process, captures its stdout in the activity feed
- Daemon starts OpenClaw gateway, monitors its health
- Kill Hermes process manually: daemon detects within 10 seconds, logs the crash, restarts per policy
- Monitoring surface shows managed process status and allows start/stop

---

## Phase 5: Project Discovery + Routing Policy

**Goal:** All projects on this Dell are discovered, classified, and assigned routing policies. Work dispatched in a project context uses that project's policy.

**Entry criteria:** Phase 2 complete.

**Tasks:**
- [ ] Implement project scanner:
  - Read Codex `config.toml` trusted projects (20 already known)
  - Read Claude scheduled task definitions for project roots
  - Read VS Code recent workspaces
  - Scan `C:\Users\Couch\` for project markers (.git, package.json, CLAUDE.md, AGENTS.md)
  - Scan `D:\SharedRoot\` for project directories
  - Scan `C:\Users\Couch\Documents\Claude\Projects\` for Cowork project folders
- [ ] Implement project classifier:
  - Domain detection from contents (package.json = coding, client folder path = client_work, etc.)
  - Consequence assignment (client work = high, personal = low, example-rebuild = critical)
- [ ] Implement per-project routing policy:
  - Default model tier ceiling based on consequence
  - Allowed/forbidden services per project
  - Receipt requirement level
- [ ] Implement `/dispatch` project context: when dispatching work, pass project_id to apply project-specific routing
- [ ] Add project browser to monitoring surface: list all projects, edit classifications, view per-project activity

**Acceptance criteria:**
- All 20 Codex trusted projects appear in the project registry
- Filesystem scan discovers additional projects
- Each project has domain and consequence classification
- Dispatching work with a project_id applies that project's routing policy

---

## Phase 6: Daily Autonomous Refresh

**Goal:** A scheduled task runs the full discovery sweep daily, detects drift, and updates state.

**Entry criteria:** Phase 1 and Phase 5 complete.

**Tasks:**
- [ ] Implement refresh routine: re-runs auth discovery, model inventory, project scan, process health check
- [ ] Implement drift detection: compare current state to previous state, log differences
  - New models added to Ollama
  - Auth expired on a provider
  - New projects created
  - Processes that were running are now gone
  - New AI tools installed
- [ ] Implement drift report: written to discovery_log table and shown on monitoring surface
- [ ] Schedule via Windows Task Scheduler (not dependent on Claude Cowork scheduler, since the daemon should be above it)
- [ ] Optionally: daemon can run refresh on its own internal timer

**Acceptance criteria:**
- Pull a new Ollama model: next refresh detects it and adds to model registry
- Expire Claude auth (logout): next refresh detects and creates repair queue entry
- Create a new project folder with a .git: next refresh discovers it

---

## Phase 7: Full Integration

**Goal:** The daemon is the operational layer. All AI services are managed. The monitoring surface is the primary operational view. The system is self-healing and persistent.

**Entry criteria:** Phases 0-6 complete.

**Tasks:**
- [ ] Register daemon as Windows startup task or Windows service
- [ ] Implement graceful shutdown: daemon stops managed child processes cleanly
- [ ] Implement self-healing: auto-restart crashed managed processes, auto-repair known issues (restart Ollama if API down, re-auth prompts for expired sessions)
- [ ] Implement notification: alert via system tray notification or monitoring surface when something needs Matt's attention
- [ ] Implement full monitoring surface:
  - Process status (live)
  - Activity feed (real-time, filtered, searchable)
  - Model roster (with ratings, sortable)
  - Provider health (with repair actions)
  - Project browser (with policy editor)
  - Routing log (every dispatch decision with reason)
  - Cost/usage summary (token volumes by service, by project)
  - CLI passthrough (terminal tabs for direct interaction with any managed tool)
- [ ] Comprehensive test: all Definition of Done criteria verified

**Acceptance criteria:**
All items from the Definition of Done section.

---

# TECH STACK

| Component | Choice | Why |
|-----------|--------|-----|
| Daemon | Python 3.13 + asyncio | Already installed, psutil available, subprocess management, async I/O |
| Process monitoring | psutil | Already installed (hermes-agent dependency), cross-platform process inspection |
| Log tailing | aiofiles + watchdog or custom tail | Async file watching for real-time log ingestion |
| State store | SQLite (aiosqlite) | Zero-config, file-based, WAL mode for concurrent reads |
| Web server | FastAPI or aiohttp | Lightweight, async, WebSocket support, serves static files |
| WebSocket | Built into FastAPI/aiohttp | Real-time event push to monitoring surface |
| Monitoring UI | HTML/JS (vanilla or lightweight framework) | Served as static files by the daemon. No build step needed for MVP. |
| Process launch | subprocess + asyncio | Launch and manage child processes with piped I/O |
| Scheduling | Windows Task Scheduler (for daily refresh) + internal asyncio timers | OS-level scheduling for the refresh, daemon-internal for polling |

**No external infrastructure required.** No Docker. No Node.js build. No npm install for the daemon. Pure Python + browser.

---

# FILE STRUCTURE

```
C:\Users\Couch\.ai-opsys\
    daemon.py                 # Main daemon entry point
    config.yaml               # Daemon configuration (poll intervals, managed services, log paths)
    inventory.sqlite          # State store
    
    core/
        process_manager.py    # Process discovery, grouping, health monitoring
        log_ingestor.py       # Log file tailing and parsing
        api_poller.py         # Ollama API, OpenClaw health, etc.
        cli_prober.py         # claude auth status, gh auth status, etc.
        router.py             # Routing engine
        dispatcher.py         # Work dispatch to adapters
        state.py              # SQLite operations
        discovery.py          # Auth, model, project discovery
        repair.py             # Repair queue management
    
    adapters/
        ollama.py             # Local model dispatch via HTTP
        claude_cli.py         # Claude Code CLI dispatch
        codex_cli.py          # Codex CLI dispatch
        hermes.py             # Hermes launch + dispatch
        openclaw.py           # OpenClaw launch + dispatch
        copilot.py            # Copilot status + dispatch
    
    ui/
        index.html            # Monitoring surface (single page)
        app.js                # WebSocket client, DOM updates
        style.css             # Minimal styling
    
    logs/
        daemon.log            # Daemon's own log
```

---

# CONSTRAINTS

- **No API keys created or used.** Cloud access through subscription client apps only.
- **No heavy framework dependencies.** Python standard library + psutil + aiosqlite + FastAPI. No React build chain for MVP.
- **Self-discovering.** The daemon finds its own environment by reading processes, log files, configs, and CLI outputs.
- **Above the apps.** The daemon does not depend on any single AI app being functional. If Claude Desktop crashes, the daemon continues managing everything else.
- **Persistent.** Survives app restarts. State in SQLite persists across daemon restarts. Designed to run continuously.
- **Progressive.** Each phase delivers a working, testable increment. Phase 0 alone provides value (process visibility).

---

# PREDECESSOR RETIREMENT

- `AI_GOVERNOR_SPEC_2026-06-04.md` v1.0 and v2.0: Superseded by this v3.0 in the same file path. v1 was an app-wrapper. v2 was a client-app-router. Both were the wrong architecture. This v3 is a process orchestration layer.
- `MSI_AI_ROSTER_2026-06-04.md`: Already marked SUPERSEDED.
- `DELL_AI_MODEL_ROSTER_2026-06-04.md`: NOT superseded. Data source for the daemon's model registry.

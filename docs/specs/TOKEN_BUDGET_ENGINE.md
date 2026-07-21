# Token Conservation Engine — Technical Design

Created: 2026-07-05 (revised from Token Budget Engine v1)
Status: DRAFT — pending owner review
Supersedes: Token Budget Engine v1 (rationing approach, withdrawn)
Extends: `TOKEN_EFFICIENCY_SPEC.md` (waste catalog) and `efficiency_policy.json` (policy)

## 1. Why the first design was wrong

The first design (Token Budget Engine) was supply-side rationing: cap
`num_predict` / `max_tokens` per job class. That approach:

- **Doesn't prevent waste** — it truncates output after the model already paid
  for the input. A 40k bloated prompt still costs 40k tokens whether the output
  cap is 256 or 4500.
- **Doesn't address the 25 waste modes** — 19 of the 25 modes are on the input
  side (re-reading, unfiltered output, whole-file reads, duplicate context).
  Capping output doesn't touch them.
- **Doesn't use the harnesses** — the spec says "deterministic work goes to a
  script," "search first, read only the needed section," "cheapest adequate
  tool." Those are *prevention methods*, not caps.

The right approach is **demand-side conservation**: reduce what goes into the
model's context before dispatch, calibrate effort to task complexity, and
detect waste after dispatch to improve future conservation.

## 2. Design principle

**Prevent waste before dispatch. Ration only as a safety net.**

The engine's job is to minimize the tokens that enter the model's context
window by:
1. Preparing minimal context (search, don't dump)
2. Injecting output directives (answer-first, no preamble)
3. Calibrating model effort to task difficulty
4. Routing toward workers that historically conserve tokens for this job class
5. Detecting waste post-dispatch and feeding it back

Output caps remain as a **safety net** (prevent runaway generation), not the
primary conservation mechanism.

## 3. The five conservation levers

### Lever 1: Prompt minimization harness

**Problem**: The current dispatch path sends `intent.raw_text` directly to the
adapter as the prompt. There's no preprocessing — the raw user text is the
prompt. If the user pastes a 20k log dump, that's 20k tokens the model pays for
every turn.

**Current flow** (dispatcher.py:184):
```
envelope = {
    "intent": intent.model_dump(mode="json"),  # contains raw_text
    "dispatch_id": ...,
    "model": ...,
    ...
}
adapter.dispatch(envelope) → reads envelope["intent"]["raw_text"]
```

**Conservation method**: A `PromptMinimizer` that runs before the envelope is
built, transforming the raw prompt into a minimized version:

- **Strip log/output dumps**: if the prompt contains >2k chars of log-like
  content (lines matching `\d{4}-\d{2}-\d{2}` timestamps, `[INFO]`, `[ERROR]`,
  stack traces), extract only the relevant lines (errors, exceptions, the
  user's actual question) and replace the rest with a summary: `[... {N} lines
  of log output omitted; {N} error lines retained]`.
- **Truncate file pastes**: if the prompt contains a pasted file >500 lines,
  replace with: `[File {name}: {total_lines} lines. First 50 lines shown;
  full file at {path} if needed.]` — the model can ask for specific sections.
- **Deduplicate repeated content**: if the prompt contains the same block
  repeated (common in error logs), collapse to one instance.
- **Preserve the question**: the user's actual instruction/question is always
  kept verbatim. Only supporting context is minimized.

**Interface**:

```python
class PromptMinimizer:
    def minimize(self, raw_text: str, job_class: str) -> MinimizedPrompt:
        # Returns the minimized prompt + what was removed (for logging)
        ...

@dataclass(frozen=True)
class MinimizedPrompt:
    text: str                          # the minimized prompt to send
    original_tokens: int               # estimated tokens before
    minimized_tokens: int              # estimated tokens after
    reduction_ratio: float             # minimized / original
    transformations: list[str]       # what was done, e.g. ["stripped_logs:15k", "truncated_file:auth.py"]
```

The minimized prompt goes into `envelope["prompt"]` (a new key, separate from
`intent.raw_text` which stays as the original for audit). Adapters read
`envelope.get("prompt") or envelope["intent"]["raw_text"]` — preferring the
minimized version when available.

### Lever 2: Context preparation harness (GATE 0)

**Problem**: The spec says "Read existing project docs and memory before any
external research; research only genuine gaps." But the dispatcher doesn't do
this — it sends the raw prompt to the model and lets the model figure out
context from scratch.

**Conservation method**: Before dispatch, check whether the orchestrator
already has relevant context on disk (project memory, prior dispatch outputs,
receipts) and inject only the relevant excerpts into the prompt — not the
full files.

**What this does NOT do**: This is not "load everything into context." It's
the opposite — it's a targeted search that replaces "let the model read the
whole repo" with "here are the 3 relevant lines from 2 files."

**Interface**:

```python
class ContextHarness:
    def prepare(
        self,
        prompt: str,
        job_class: str,
        project_id: str | None,
        store: StateStore,
    ) -> PreparedContext:
        # 1. Search project docs/memory for relevant excerpts
        # 2. Search prior dispatch receipts for similar tasks
        # 3. If relevant context found, append as a "Context" section
        # 4. If no relevant context found, return empty (don't inject noise)
        ...

@dataclass(frozen=True)
class PreparedContext:
    augmented_prompt: str              # original prompt + relevant context
    context_sources: list[str]         # what was found and included
    context_tokens: int               # tokens added by context preparation
    skipped_sources: list[str]        # what was searched but not relevant
```

**Scope**: The initial implementation searches:
- The orchestrator's own `receipts` table for prior dispatches on the same
  project (the output_summary field is already there)
- Project working memory if available

This is a conservative first step — it only injects context the orchestrator
already has, not external file reads (which are the adapter's job).

### Lever 3: Output directive injection

**Problem**: The `always_on_rules` in `efficiency_policy.json` are currently
only compiled into agent instruction stubs (CLAUDE.md, AGENTS.md) via
`generate-surface-stubs.ps1`. They are NOT injected at dispatch time for
direct adapter calls. So when the dispatcher sends a prompt to Ollama or LM
Studio, the model gets no guidance about output format — it produces whatever
it wants, including preamble, recaps, and over-formatting (waste modes 13-18).

**Conservation method**: Inject a compact output directive into the prompt
based on the job class's ceremony tier. The directive is ~50 tokens, not a
full system prompt — it's a targeted instruction that prevents the most
common output waste.

**Interface**:

```python
class OutputDirective:
    def directive_for(self, job_class: str) -> str:
        # Returns a 1-2 sentence output directive
        # Based on ceremony_tiers from efficiency_policy.json
        ...

# Examples:
# routing_triage → "Answer in one line. No preamble."
# repo_coding → "Output the code change only. No explanation unless asked."
# summarization → "Provide the summary. No preamble. No 'Here is...' intro."
# architecture → "Provide the design. Structure with headings. No preamble."
```

The directive is prepended to the prompt (or set as the system message for
adapters that support system messages). This is waste mode 17 ("mandatory
per-response ceremony") and 13 ("preamble/recap/postamble padding") prevention.

### Lever 4: Effort calibration

**Problem**: The Codex adapter always uses `model_reasoning_effort='low'`.
The Claude adapter always uses `--max-budget-usd 0.25`. These are hard-coded
regardless of whether the task is "classify this sentence" or "design a
distributed system." Waste mode 19: "max reasoning effort on trivial tasks."

**Conservation method**: Calibrate the model's effort level to the job class.
Not just the output cap — the *reasoning effort*, which affects how many
internal tokens the model generates before producing output.

**Interface**:

```python
class EffortCalibrator:
    def calibrate(self, job_class: str) -> EffortLevel:
        ...

@dataclass(frozen=True)
class EffortLevel:
    reasoning_effort: str             # "low" | "medium" | "high"
    max_output_tokens: int | None      # safety net cap (not primary conservation)
    budget_usd: float | None           # for adapters that support USD caps

# Mapping:
# routing_triage, quick_question, classification → low, 512 tokens
# repo_coding, simple_coding, bulk_extraction → medium, 4096 tokens
# architecture, deep_debugging, long_context_synthesis → high, None (let it think)
```

The `max_output_tokens` here is a **safety net**, not the primary conservation
mechanism. It prevents runaway generation but is set generously enough that it
doesn't truncate legitimate output. The primary conservation is the prompt
minimization and directive injection.

**Adapter integration**:

| Adapter | Effort parameter | How to set |
|---|---|---|
| `CodexExecAdapter` | `model_reasoning_effort` | Already exists, currently hard-coded `'low'` |
| `ClaudePrintAdapter` | `--max-budget-usd` | Already exists, currently hard-coded `0.25` |
| `OllamaHttpAdapter` | `num_predict` | Already exists, currently hard-coded `256` |
| `LmStudioAdapter` | `max_tokens` | Already exists, currently hard-coded `128` |
| `GeminiCliAdapter` | none | Prompt directive only |
| `CopilotCliAdapter` | none | Prompt directive only |

### Lever 5: Post-dispatch waste detection and feedback

**Problem**: After dispatch, the system records `tokens_in` and `tokens_out`
but never analyzes *why* they were consumed. If a dispatch used 45k tokens and
the output was a 200-token answer, 44.8k tokens were context — was that
necessary? The system doesn't know.

**Conservation method**: After dispatch, analyze the output for waste signals
and record a conservation score. This feeds back into routing (prefer workers
that historically conserve) and into the prompt minimizer (learn which
transformations are effective).

**Waste signals detected**:

| Signal | Detection | What it means |
|---|---|---|
| Preamble ratio | Output starts with "Here is", "Sure", "I'll", "Let me" | Waste mode 13 |
| Recap ratio | Output contains "As you asked", "To summarize your request" | Waste mode 13 |
| Useful content ratio | (output_tokens - preamble_tokens - recap_tokens) / output_tokens | Low ratio = high waste |
| Retry overhead | tokens_in across all attempts vs final output | Retry waste (mode 21) |
| Over-context ratio | tokens_in / (estimated_prompt_tokens) | High ratio = unnecessary context loaded |

**Interface**:

```python
class WasteDetector:
    def analyze(
        self,
        result: dict[str, Any],
        envelope: dict[str, Any],
        attempts: list[dict[str, Any]],
        minimized_prompt: MinimizedPrompt,
    ) -> ConservationReport:
        ...

@dataclass(frozen=True)
class ConservationReport:
    useful_content_ratio: float       # 0.0-1.0, fraction of output that was useful
    preamble_tokens: int             # tokens wasted on preamble
    retry_waste_tokens: int          # tokens wasted on failed attempts
    over_context_ratio: float        # input tokens / estimated necessary
    conservation_score: float        # 0.0-1.0 composite (higher = more efficient)
    signals: list[str]               # human-readable waste signals detected
```

The `conservation_score` feeds into the routing engine's
`_token_efficiency_score` — replacing the current naive `1.0 - (avg / 8192)`
with a real waste-aware metric. This is the feedback loop.

## 4. Architecture

### 4.1 Component overview

```
                    ┌─────────────────────────────────┐
                    │  efficiency_policy.json         │
                    │  context_budgets_tokens         │
                    │  ceremony_tiers                  │
                    │  always_on_rules                 │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │  TokenConservationEngine         │
                    │  ├─ PromptMinimizer              │
                    │  ├─ ContextHarness (GATE 0)      │
                    │  ├─ OutputDirective              │
                    │  ├─ EffortCalibrator              │
                    │  └─ WasteDetector                │
                    └──┬──────────┬──────────┬─────────┘
                       │          │          │
          ┌────────────┘    │     └────────────┐
          │                 │                  │
   ┌──────▼──────┐  ┌──────▼───────┐  ┌────────▼────────┐
   │ Dispatcher   │  │ Adapter      │  │ Routing         │
   │ (runs engine │  │ (reads       │  │ (uses           │
   │  pre-dispatch│  │  minimized   │  │  conservation   │
   │  + post)     │  │  prompt +    │  │  score instead  │
   │              │  │  effort lvl) │  │  of naive avg)  │
   └──────────────┘  └──────────────┘  └─────────────────┘
```

### 4.2 TokenConservationEngine (new module)

**Location**: `orchestrator/usage/conservation.py`

This is the orchestrator — it runs the five levers in sequence around the
existing dispatch path:

```python
class TokenConservationEngine:
    """Runs conservation methods before and after adapter dispatch."""

    def __init__(self, repo_root: Path, store: StateStore):
        self.minimizer = PromptMinimizer()
        self.context = ContextHarness(store)
        self.directive = OutputDirective(repo_root)
        self.calibrator = EffortCalibrator()
        self.detector = WasteDetector()

    async def prepare_dispatch(
        self,
        raw_text: str,
        job_class: str,
        project_id: str | None,
    ) -> PreparedDispatch:
        # Lever 1: minimize the prompt
        minimized = self.minimizer.minimize(raw_text, job_class)

        # Lever 2: prepare relevant context (GATE 0)
        context = await self.context.prepare(
            minimized.text, job_class, project_id, self.store
        )

        # Lever 3: inject output directive
        directive_text = self.directive.directive_for(job_class)
        final_prompt = self._compose_prompt(
            context.augmented_prompt, directive_text
        )

        # Lever 4: calibrate effort
        effort = self.calibrator.calibrate(job_class)

        return PreparedDispatch(
            prompt=final_prompt,
            effort=effort,
            minimized=minimized,
            context=context,
            directive=directive_text,
        )

    def analyze_dispatch(
        self,
        result: dict[str, Any],
        envelope: dict[str, Any],
        attempts: list[dict[str, Any]],
        prepared: PreparedDispatch,
    ) -> ConservationReport:
        # Lever 5: detect waste and produce conservation score
        return self.detector.analyze(result, envelope, attempts, prepared.minimized)
```

### 4.3 PreparedDispatch (what goes into the envelope)

```python
@dataclass(frozen=True)
class PreparedDispatch:
    prompt: str                       # the final minimized + context + directive prompt
    effort: EffortLevel               # reasoning effort + safety-net output cap
    minimized: MinimizedPrompt        # for logging/audit
    context: PreparedContext          # what context was found and injected
    directive: str                    # the output directive injected

    def envelope_extras(self) -> dict[str, Any]:
        """The keys to merge into the dispatch envelope."""
        return {
            "prompt": self.prompt,                     # adapters read this first
            "token_effort": {
                "reasoning_effort": self.effort.reasoning_effort,
                "max_output_tokens": self.effort.max_output_tokens,
                "budget_usd": self.effort.budget_usd,
            },
            "conservation_audit": {
                "original_tokens": self.minimized.original_tokens,
                "minimized_tokens": self.minimized.minimized_tokens,
                "reduction_ratio": self.minimized.reduction_ratio,
                "transformations": self.minimized.transformations,
                "context_sources": self.context.context_sources,
                "context_tokens": self.context.context_tokens,
                "directive": self.directive,
            },
        }
```

### 4.4 Dispatcher integration

In `_execute_intent`, after job classification and before the dispatch loop:

```python
# BEFORE (current):
envelope = {
    "intent": intent.model_dump(mode="json"),
    "dispatch_id": dispatch_id,
    ...
}

# AFTER:
conservation_engine = TokenConservationEngine(self.settings.repo_root, self.store)
prepared = await conservation_engine.prepare_dispatch(
    intent.raw_text, job_class, intent.project_id
)
envelope = {
    "intent": intent.model_dump(mode="json"),  # raw_text stays for audit
    "prompt": prepared.prompt,                  # minimized prompt for the model
    "dispatch_id": dispatch_id,
    "token_effort": prepared.effort.__dict__,   # effort calibration
    "conservation_audit": ...,                  # for logging
    ...
}
```

After completion, in `_complete_dispatch`:

```python
report = conservation_engine.analyze_dispatch(
    result, envelope, attempts, prepared
)
receipt["conservation_report"] = report.__dict__
await self.store.record_conservation_report(dispatch_id, job_class, report)
```

### 4.5 Adapter changes

Each adapter reads `envelope.get("prompt")` instead of (or before)
`envelope["intent"]["raw_text"]`, and reads `envelope.get("token_effort")` for
effort calibration:

```python
# Ollama example:
prompt = str(
    envelope.get("prompt")  # ← minimized prompt (new)
    or envelope.get("intent", {}).get("raw_text")  # ← fallback (existing)
    or envelope.get("prompt")  # ← legacy fallback
    or ""
).strip()
effort = envelope.get("token_effort") or {}
num_predict = int(effort.get("max_output_tokens") or 256)  # ← was hard-coded 256
```

**Backward compatibility**: if `prompt` or `token_effort` is absent from the
envelope (proof mode, direct adapter proof), adapters fall back to existing
behavior. No breaking change.

## 5. Implementation details per lever

### Lever 1: PromptMinimizer

```python
class PromptMinimizer:
    LOG_PATTERN = re.compile(
        r"^\s*(?:\d{4}-\d{2}-\d{2}|\[\d{4}-\d{2}-\d{2}|Traceback|  File |"
        r"\[INFO\]|\[ERROR\]|\[WARN\]|\[DEBUG\])",
        re.MULTILINE,
    )
    FILE_PASTE_THRESHOLD = 500  # lines

    def minimize(self, raw_text: str, job_class: str) -> MinimizedPrompt:
        original = estimate_tokens(raw_text)
        text = raw_text
        transformations = []

        # 1. Strip log dumps: keep only error/exception lines
        log_lines = self._extract_log_lines(text)
        if log_lines and len(log_lines["full"]) > 2000:
            text = text.replace(
                log_lines["block"],
                f"[... {log_lines['total']} lines of log output; "
                f"{log_lines['errors']} error/exception lines retained:]\n"
                + log_lines["errors_only"]
            )
            transformations.append(
                f"stripped_logs:{log_lines['total'] - log_lines['errors']}_lines"
            )

        # 2. Truncate pasted files
        file_pastes = self._detect_file_pastes(text)
        for paste in file_pastes:
            if paste["line_count"] > self.FILE_PASTE_THRESHOLD:
                replacement = (
                    f"[File {paste['name']}: {paste['line_count']} lines. "
                    f"First 50 lines shown. Provide specific sections if needed.]"
                )
                text = text.replace(paste["block"], replacement)
                transformations.append(
                    f"truncated_file:{paste['name']}:{paste['line_count']}_to_50"
                )

        # 3. Deduplicate repeated blocks
        text, dedup_count = self._deduplicate(text)
        if dedup_count:
            transformations.append(f"deduplicated:{dedup_count}_blocks")

        minimized = estimate_tokens(text)
        return MinimizedPrompt(
            text=text,
            original_tokens=original,
            minimized_tokens=minimized,
            reduction_ratio=minimized / max(original, 1),
            transformations=transformations,
        )
```

### Lever 2: ContextHarness

```python
class ContextHarness:
    def __init__(self, store: StateStore):
        self.store = store

    async def prepare(
        self,
        prompt: str,
        job_class: str,
        project_id: str | None,
        store: StateStore,
    ) -> PreparedContext:
        context_sources = []
        skipped_sources = []
        augmented = prompt

        if project_id:
            # Search prior dispatch receipts on this project
            prior = await store.recent_receipt_summaries(project_id, limit=5)
            relevant = self._filter_relevant(prior, prompt, job_class)
            if relevant:
                context_block = self._format_prior_context(relevant)
                augmented = f"{prompt}\n\n--- Prior context ---\n{context_block}"
                context_sources.append(f"prior_receipts:{len(relevant)}")
            else:
                skipped_sources.append("prior_receipts:none_relevant")

        return PreparedContext(
            augmented_prompt=augmented,
            context_sources=context_sources,
            context_tokens=estimate_tokens(augmented) - estimate_tokens(prompt),
            skipped_sources=skipped_sources,
        )
```

**Scope**: The initial implementation only queries the orchestrator's own
receipts table (which already has `output_summary`, `job_class`, `project_id`
via the intent). It does not search external files — that's the adapter's job
(via tools). This is a conservative first step that prevents re-dispatching
work the orchestrator already has outputs for.

### Lever 3: OutputDirective

```python
class OutputDirective:
    DIRECTIVES: dict[str, str] = {
        "routing_triage": "Answer in one line. No preamble.",
        "quick_question": "Answer directly. No preamble. No 'Here is' intro.",
        "classification": "Provide the classification only. No explanation.",
        "summarization": "Provide the summary. No preamble. No recap.",
        "simple_coding": "Output the code only. Brief comments if non-obvious.",
        "repo_coding": "Output the code change. Explain only the non-obvious parts.",
        "agentic_repair": "Report the fix. Show the diff. Explain the root cause briefly.",
        "bulk_extraction": "Output the extracted data in the requested format. No preamble.",
        "documentation": "Provide the documentation. No preamble.",
        "architecture": "Provide the design. Use headings for structure. No preamble.",
        "deep_debugging": "Report findings. Show evidence. No preamble.",
        "long_context_synthesis": "Provide the synthesis. Use structure for multi-part output.",
        "default": "Answer directly. No preamble. No recap.",
    }

    def directive_for(self, job_class: str) -> str:
        return self.DIRECTIVES.get(job_class, self.DIRECTIVES["default"])
```

The directive is prepended as a system message (for adapters that support
system messages) or as a prefix to the prompt (for adapters that don't). It's
~15 tokens — far cheaper than the waste it prevents.

### Lever 4: EffortCalibrator

```python
class EffortCalibrator:
    EFFORT_MAP: dict[str, EffortLevel] = {
        "routing_triage": EffortLevel("low", 512, 0.10),
        "quick_question": EffortLevel("low", 512, 0.10),
        "classification": EffortLevel("low", 512, 0.10),
        "summarization": EffortLevel("low", 2048, 0.15),
        "simple_coding": EffortLevel("medium", 4096, 0.20),
        "repo_coding": EffortLevel("medium", 8192, 0.25),
        "agentic_repair": EffortLevel("medium", 8192, 0.25),
        "bulk_extraction": EffortLevel("medium", 4096, 0.20),
        "documentation": EffortLevel("medium", 4096, 0.20),
        "architecture": EffortLevel("high", None, 0.50),
        "deep_debugging": EffortLevel("high", 16384, 0.50),
        "long_context_synthesis": EffortLevel("high", None, 0.50),
        "default": EffortLevel("medium", 4096, 0.25),
    }

    def calibrate(self, job_class: str) -> EffortLevel:
        return self.EFFORT_MAP.get(job_class, self.EFFORT_MAP["default"])
```

Note: `max_output_tokens=None` means "no safety-net cap" — for architecture and
synthesis tasks where the output is the deliverable, we don't cap. The
conservation comes from prompt minimization and directive injection, not from
truncating the output.

### Lever 5: WasteDetector

```python
class WasteDetector:
    PREAMBLE_PATTERNS = [
        r"^(?:Here is|Here's|Sure[!,]?|I'll|Let me|Certainly|Of course|Absolutely)\b",
        r"^(?:As (?:you|requested)|To (?:summarize|address) your)",
        r"^(?:Based on|After (?:analyzing|reviewing))",
    ]
    RECAP_PATTERNS = [
        r"(?:To summarize your request|As you asked|You requested)",
    ]

    def analyze(
        self,
        result: dict[str, Any],
        envelope: dict[str, Any],
        attempts: list[dict[str, Any]],
        minimized: MinimizedPrompt,
    ) -> ConservationReport:
        output = str(result.get("text") or "")
        output_tokens = estimate_tokens(output)

        # Detect preamble
        preamble_tokens = self._detect_preamble_tokens(output)

        # Detect recap
        recap_tokens = self._detect_recap_tokens(output)

        # Retry waste
        retry_waste = sum(
            int(((a.get("detail") or {}).get("token_usage") or {}).get("tokens_total") or 0)
            for a in attempts
            if a.get("state") != "completed"
        )

        # Over-context ratio
        input_tokens = sum(
            int(((a.get("detail") or {}).get("token_usage") or {}).get("tokens_in") or 0)
            for a in attempts
        )
        over_context = input_tokens / max(minimized.minimized_tokens, 1)

        useful = output_tokens - preamble_tokens - recap_tokens
        useful_ratio = max(0.0, useful / max(output_tokens, 1))

        # Conservation score: penalize waste signals
        score = useful_ratio * 0.5
        score += max(0, 1.0 - over_context / 3.0) * 0.25  # over-context penalty
        score += max(0, 1.0 - retry_waste / max(output_tokens, 1)) * 0.25  # retry penalty
        score = max(0.0, min(1.0, score))

        signals = []
        if preamble_tokens > output_tokens * 0.15:
            signals.append(f"high_preamble_ratio:{preamble_tokens}/{output_tokens}")
        if retry_waste > output_tokens:
            signals.append(f"retry_waste:{retry_waste}_tokens")
        if over_context > 3.0:
            signals.append(f"over_context:{over_context:.1f}x")

        return ConservationReport(
            useful_content_ratio=useful_ratio,
            preamble_tokens=preamble_tokens,
            retry_waste_tokens=retry_waste,
            over_context_ratio=over_context,
            conservation_score=score,
            signals=signals,
        )
```

## 6. Routing integration

### 6.1 Replace token_efficiency_score with conservation_score

The current `_token_efficiency_score` (worker_routing.py:415) uses
`1.0 - (avg / 8192)` — an arbitrary denominator. Replace it with the
conservation score from post-dispatch analysis:

```python
def _conservation_efficiency_score(
    worker: dict[str, Any],
    job_class: str,
    conservation_summary: dict[str, dict[str, Any]],
) -> float:
    """Score worker by historical conservation performance for this job class."""
    provider = str(worker.get("provider_id") or worker.get("surface") or "")
    model = str(worker.get("model_id") or "")
    key = f"{provider}|{model}|{job_class}"
    summary = conservation_summary.get(key)
    if not summary or int(summary.get("samples") or 0) < 3:
        return 0.65  # neutral default
    avg_score = float(summary.get("avg_conservation_score") or 0.65)
    return max(0.0, min(1.0, avg_score))
```

This scores a worker on *how efficiently it completed this job class
historically* — not just raw token count. A worker that produces clean output
with no preamble scores higher than one that generates the same answer with
2k tokens of "Here is the answer to your question..."

### 6.2 Weight rebalance

Current composite (worker_routing.py:560):

```
token_score * 0.06  (was nearly invisible)
```

Proposed:

```
conservation_score * 0.12  (doubled, and now measures real waste not just token count)
```

The weight is taken from `contract_score` (0.18 → 0.16) and `stat_score`
(0.10 → 0.08). The total stays 1.0.

### 6.3 Context-fit gate (kept from v1, as a safety check)

The context-fit rejection gate from the first design is still valuable — but
framed as a safety check, not the primary conservation mechanism:

```python
# In route_with_workers, after capability match:
context_window = int(worker.get("context_window") or 0)
if context_window > 0:
    input_estimate = prepared.minimized.minimized_tokens
    output_floor = prepared.effort.max_output_tokens or 4096
    if input_estimate + output_floor > context_window:
        row["rejected_reason"] = (
            f"context_window {context_window:,} < "
            f"input {input_estimate:,} + output_floor {output_floor:,}"
        )
        rejected.append(row)
        continue
```

This uses the *minimized* prompt estimate, not the raw prompt — so a 20k log
dump that was minimized to 2k doesn't cause false rejections.

## 7. State store additions

### 7.1 New table: conservation_reports

```sql
CREATE TABLE IF NOT EXISTS conservation_reports (
    id TEXT PRIMARY KEY,
    dispatch_id TEXT REFERENCES dispatches(id),
    job_class TEXT NOT NULL,
    worker_id TEXT,
    provider TEXT,
    model TEXT,
    useful_content_ratio REAL,
    preamble_tokens INTEGER,
    retry_waste_tokens INTEGER,
    over_context_ratio REAL,
    conservation_score REAL,
    signals_json TEXT,
    original_prompt_tokens INTEGER,
    minimized_prompt_tokens INTEGER,
    reduction_ratio REAL,
    transformations_json TEXT,
    context_sources_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cr_job_worker
    ON conservation_reports(job_class, worker_id, created_at);
```

### 7.2 New store methods

```python
async def record_conservation_report(
    self, dispatch_id: str, job_class: str, worker_id: str,
    report: ConservationReport, prepared: PreparedDispatch
) -> None: ...

async def conservation_summary(
    self, limit: int = 1000
) -> dict[str, dict[str, Any]]:
    """Aggregate conservation scores by provider|model|job_class for routing."""
    ...

async def recent_receipt_summaries(
    self, project_id: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Return recent receipt output_summaries for a project (for GATE 0)."""
    ...
```

## 8. What changes vs. what stays

### Changes:
| File | Change |
|---|---|
| `orchestrator/usage/conservation.py` | **NEW** — the engine, 5 lever modules |
| `orchestrator/state/schema.sql` | Add `conservation_reports` table |
| `orchestrator/state/store.py` | Add conservation methods + receipt summaries |
| `orchestrator/adapters/builtins.py` | Adapters read `envelope["prompt"]` + `envelope["token_effort"]` |
| `orchestrator/dispatch/dispatcher.py` | Run engine pre-dispatch, record post-dispatch |
| `orchestrator/routing/worker_routing.py` | Use conservation_score, raise weight to 0.12 |
| `tests/test_conservation_engine.py` | **NEW** — unit tests for all 5 levers |
| `tests/test_dispatch.py` | Add tests for conservation integration |

### Stays the same:
- `efficiency_policy.json` — consumed as-is (we read `ceremony_tiers` and
  `always_on_rules` in addition to `context_budgets_tokens`)
- `token_usage` table — stays as per-attempt accounting
- `always_on_rules` — still compiled into agent stubs via the PS1 script
- Routing ordering ladder — unchanged, only composite weights shift
- Adapter dispatch signatures — unchanged, just reading new envelope keys
- Proof mode — conservation engine runs but doesn't block

## 9. Data flow (end to end)

```
1. User dispatches: "fix the login bug in auth.py [pastes 500-line file]"
   ↓
2. classify_intent → job_class = "repo_coding"
   ↓
3. TokenConservationEngine.prepare_dispatch():
   ├─ Lever 1 (PromptMinimizer):
   │  → detects 500-line file paste, truncates to 50 lines + pointer
   │  → original_tokens=8000, minimized_tokens=1200, reduction=0.15
   ├─ Lever 2 (ContextHarness):
   │  → searches prior receipts for auth.py on this project
   │  → finds 2 relevant prior dispatches, injects summaries (200 tokens)
   ├─ Lever 3 (OutputDirective):
   │  → injects: "Output the code change. Explain only the non-obvious parts."
   │  → ~15 tokens
   └─ Lever 4 (EffortCalibrator):
      → reasoning_effort=medium, max_output_tokens=8192, budget_usd=0.25
   ↓
4. Envelope now contains:
   - intent.raw_text = original (for audit)
   - prompt = minimized + context + directive (1415 tokens, was 8000)
   - token_effort = {reasoning_effort: medium, max_output_tokens: 8192}
   ↓
5. Routing:
   - Uses conservation_score (from prior dispatches) instead of naive token avg
   - Context-fit gate uses minimized_tokens (1200) not original (8000)
   → selects worker with best conservation history for repo_coding
   ↓
6. Adapter dispatch:
   - reads envelope["prompt"] (minimized, not raw)
   - sets num_predict=8192 (effort-calibrated, not hard-coded 256)
   ↓
7. Dispatch completes: in=1450, out=2100, total=3550
   ↓
8. TokenConservationEngine.analyze_dispatch():
   ├─ detects: 0 preamble tokens (directive worked), useful_ratio=0.95
   ├─ retry_waste=0 (succeeded first attempt)
   ├─ over_context=1450/1200=1.2x (reasonable, context added value)
   └─ conservation_score=0.87
   ↓
9. Records to conservation_reports table
   ↓
10. Next repo_coding dispatch: routing sees this worker's conservation_score=0.87
    → prefers it over a worker with score=0.52 (high preamble, high retry waste)
```

## 10. Testing strategy

### 10.1 Unit tests (`tests/test_conservation_engine.py`)

**PromptMinimizer**:
- `test_strip_log_dump`: 5k of logs → only error lines retained
- `test_truncate_file_paste`: 600-line paste → 50 lines + pointer
- `test_deduplicate`: repeated blocks → single instance
- `test_preserve_question`: user's question always kept verbatim
- `test_no_change_for_short_prompt`: 100-token prompt → unchanged

**ContextHarness**:
- `test_injects_prior_receipt`: relevant prior receipt → injected
- `test_skips_irrelevant`: unrelated prior receipt → not injected
- `test_no_project_id`: no project → empty context (no crash)

**OutputDirective**:
- `test_directive_per_job_class`: each class → correct directive
- `test_directive_is_short`: directive < 50 tokens

**EffortCalibrator**:
- `test_low_effort_for_triage`: routing_triage → low, 512
- `test_high_effort_for_architecture`: architecture → high, None
- `test_default`: unknown class → medium, 4096

**WasteDetector**:
- `test_detect_preamble`: "Here is the answer..." → preamble_tokens > 0
- `test_detect_recap`: "As you asked..." → recap detected
- `test_no_waste_signals`: clean output → score > 0.8
- `test_retry_waste`: 2 failed attempts → retry_waste_tokens > 0
- `test_over_context`: 10x input → over_context_ratio > 3.0

### 10.2 Integration tests

- `test_minimized_prompt_in_envelope`: dispatch → envelope has minimized prompt
- `test_adapter_reads_minimized`: fake adapter asserts it got the minimized prompt
- `test_effort_calibration_in_envelope`: dispatch → envelope has token_effort
- `test_conservation_report_recorded`: after completion → conservation_reports has row
- `test_routing_uses_conservation_score`: routing prefers worker with better conservation

## 11. Risks

| Risk | Mitigation |
|---|---|
| Prompt minimizer too aggressive (strips needed context) | Preserve user's question verbatim; only strip supporting content; log transformations for audit; safety threshold (don't minimize if prompt < 500 tokens) |
| Context harness injects irrelevant context | Conservative relevance filter; only inject if prior dispatch shares keywords with current prompt; cap injected context at 500 tokens |
| Output directive constrains legitimate output | Directives are format guidance, not content limits; "no preamble" doesn't mean "no explanation" |
| Effort calibration wrong for edge cases | Default to medium; the effort map is a starting point, not exhaustive |
| Waste detector false positives | Preamble patterns are conservative; "Here is" at the start is waste, but "Here is the design:" in the middle is not |
| Conservation score biases routing toward terse workers | The score includes `useful_content_ratio`, not just brevity — a complete answer with no preamble scores higher than a terse incomplete one |

## 12. Out of scope

- **Agent host context management** (Hermes, Claude Code internal context) — those manage their own context windows; the orchestrator can't reach inside them
- **External file search** (grep, ripgrep) — that's the adapter's job via tools; the context harness only queries orchestrator state
- **Dollar cost optimization** — the system tracks tokens and quotas, not per-token dollar costs
- **Changing always_on_rules or ceremony tiers** — those are policy, not dispatch logic
- **The token_usage per-attempt table** — stays as-is; conservation_reports is additive

## 13. Implementation order (after approval)

1. `conservation.py` core + 5 lever modules + unit tests → verify each lever
2. Schema migration + store methods → verify DB migration
3. Dispatcher integration (prepare_dispatch + analyze_dispatch) → verify end-to-end
4. Adapter changes (read minimized prompt + effort) → verify each adapter
5. Routing integration (conservation_score + weight) → run existing tests
6. Full test suite → `pytest`
7. Update `TOKEN_EFFICIENCY_SPEC.md` enforcement map
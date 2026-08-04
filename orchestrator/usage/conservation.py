"""Token conservation engine — demand-side prevention.

This module is a demand-side prevention system: it minimizes what enters
context *before* dispatch (prompt minimization, context harnessing, output
directives, effort calibration) and then audits the model's output for waste
*promptly after* the dispatch completes. It is NOT supply-side rationing —
it never truncates a model's response to enforce a token cap after the model
has already paid to read the input.

The five levers:

1. :class:`PromptMinimizer` / :func:`minimize_prompt` — strip log dumps,
   truncate pasted files, dedupe repeated blocks, preserve the user's
   question verbatim.
2. :class:`ContextHarness` / :func:`harness_context` — pull the last 3 prior
   dispatch receipts for the same project from the orchestrator state DB,
   trimmed to key fields only.
3. :class:`OutputDirective` / :func:`get_output_directive` — a ~15-token
   per-job-class directive the model is told to obey.
4. :class:`EffortCalibrator` / :func:`calibrate_effort` — map job classes to
   reasoning effort + max output tokens, guided by
   ``efficiency_policy.json`` context budgets.
5. :class:`WasteDetector` / :func:`detect_waste` — score an output for
   preamble, recap, over-formatting, and ceremony.

Authoritative reference for job classes: ``orchestrator/config/efficiency_policy.json``
and the ``job_classes`` table in ``.runtime/orchestrator/state.sqlite``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from typing import Any

__all__ = [
    "PromptMinimizer",
    "minimize_prompt",
    "ContextHarness",
    "harness_context",
    "OutputDirective",
    "get_output_directive",
    "EffortCalibrator",
    "calibrate_effort",
    "WasteDetector",
    "detect_waste",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Path to the efficiency policy (relative to project root). The path is
# resolved lazily so the module remains importable from any working directory.
_EFFICIENCY_POLICY_REL = os.path.join("orchestrator", "config", "efficiency_policy.json")

# Path to the orchestrator state DB (relative to project root).
_STATE_DB_REL = os.path.join(".runtime", "orchestrator", "state.sqlite")

# The 14 canonical job classes, sourced from the ``job_classes`` table and
# ``efficiency_policy.json``. Used as the authoritative enumeration for
# directives and effort calibration.
ALL_JOB_CLASSES: tuple[str, ...] = (
    "agentic_repair",
    "architecture",
    "bulk_extraction",
    "business_sensitive",
    "deep_debugging",
    "long_context_synthesis",
    "ocr_document_intake",
    "public_content",
    "repo_coding",
    "routing_triage",
    "schema_validation",
    "security_review",
    "simple_coding",
    "visual_reasoning",
)

# Lines containing any of these substrings are considered error/diagnostic
# lines worth keeping when stripping log dumps.
_ERROR_MARKERS: tuple[str, ...] = ("Error", "Traceback", "Exception", "WARN")

# Maximum number of lines a pasted file may occupy before truncation.
_PASTED_FILE_LINE_CAP = 50

# Minimum run length for a repeated block to be deduplicated.
_REPEAT_THRESHOLD = 3


def _load_efficiency_policy(project_root: str | None) -> dict[str, Any]:
    """Load ``efficiency_policy.json`` from ``project_root``.

    Returns an empty dict if the file is missing or unreadable so callers
    always get a shape they can ``.get()`` against safely.
    """
    if not project_root:
        return {}
    path = os.path.join(project_root, _EFFICIENCY_POLICY_REL)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Lever 1 — PromptMinimizer
# ---------------------------------------------------------------------------


class PromptMinimizer:
    """Reduce a raw prompt's token footprint before it reaches the model.

    This is demand-side prevention: we shrink the *input* so the model never
    pays to read noise. Four transformations, applied in order:

    1. Log-dump stripping — collapse multi-line log output to the diagnostic
       lines only (``Error`` / ``Traceback`` / ``Exception`` / ``WARN``).
    2. Pasted-file truncation — cap pasted file contents at 50 lines and
       emit a pointer to the original path.
    3. Block deduplication — collapse 3+ identical adjacent lines to one
       representative plus a ``[repeated N times]`` marker.
    4. Question preservation — the final user message is never truncated.
    """

    # Regex that detects a pasted-file fence marker, e.g. "--- file: src/foo.py ---"
    # or "```python src/foo.py" or "File: C:\\path\\to\\file.py". Captures the path.
    _FILE_FENCE_RE = re.compile(
        r"""^\s*(?:-{3,}\s*file:\s*|```[^\n]*\s*|File:\s*)       # fence prefix
            (?P<path>[^\s`].*?)\s*                                # the path
            (?:---|```)?\s*$""",
        re.IGNORECASE | re.VERBOSE,
    )

    @classmethod
    def minimize(cls, text: str, job_class: str) -> str:
        """Return a minimized copy of ``text`` for the given job class.

        ``job_class`` currently does not change the transformations applied
        (all four run unconditionally) but is accepted so future tiers can
        tune aggressiveness per job class without changing the call site.
        """
        if not text or not text.strip():
            return text

        # Preserve the final user message verbatim. We split on blank-line
        # delimited "messages" heuristically: the last non-empty block is the
        # user's question and is exempt from truncation.
        question, body = cls._split_question(text)

        body = cls._strip_log_dumps(body)
        body = cls._truncate_pasted_files(body)
        body = cls._dedup_repeated_blocks(body)

        if question:
            return body.rstrip() + "\n\n" + question
        return body

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _split_question(text: str) -> tuple[str, str]:
        """Separate the last non-empty paragraph (the question) from the body.

        Returns ``(question, body_without_question)``. If the text is a single
        paragraph (no blank-line separators), we treat it as a body with no
        separate question — transformations still apply. Only when there are
        2+ paragraphs do we carve off the last one as the protected question.
        """
        # Normalize: split on runs of blank lines.
        paragraphs = re.split(r"\n\s*\n", text.strip())
        paragraphs = [p for p in paragraphs if p.strip()]
        if len(paragraphs) <= 1:
            # Single block: no separate question to protect — apply all
            # transformations to the full text.
            return "", text.strip()
        question = paragraphs[-1]
        body = "\n\n".join(paragraphs[:-1])
        return question, body

    # Regex that detects a log line: starts with a timestamp (date, optionally
    # with time) or begins with a log-level word. Used to decide whether a
    # multi-line block is a "log dump" (subject to error-line-only stripping)
    # versus code/content (left alone).
    _LOG_LINE_RE = re.compile(
        r"""^\s*                                # leading whitespace
            (?:                                  # either:
              \d{4}[-/]\d{2}[-/]\d{2}            #   a date YYYY-MM-DD
              (?:[T ]\d{2}:\d{2}:\d{2})?         #   optional time
              |                                  # ...or:
              (?:INFO|DEBUG|WARN(?:ING)?|ERROR|FATAL|TRACE|CRITICAL)\b  # log level as first word
            )""",
        re.IGNORECASE | re.VERBOSE,
    )

    @classmethod
    def _strip_log_dumps(cls, text: str) -> str:
        """Keep only diagnostic lines from multi-line log dumps.

        A "log dump" is a run of 4+ consecutive lines where a majority look
        like log output (timestamps or log-level words). Within such a block
        we keep only lines containing an error marker. Blocks that don't
        look like logs (code, prose) are preserved as-is.
        """
        lines = text.split("\n")
        result: list[str] = []
        block: list[str] = []

        def is_log_block(blk: list[str]) -> bool:
            if len(blk) < 4:
                return False
            non_blank = [ln for ln in blk if ln.strip()]
            if not non_blank:
                return False
            log_lines = [ln for ln in non_blank if cls._LOG_LINE_RE.match(ln)]
            return len(log_lines) >= len(non_blank) * 0.5

        def flush_block() -> None:
            if is_log_block(block):
                kept = [
                    line
                    for line in block
                    if any(marker in line for marker in _ERROR_MARKERS)
                ]
                if kept:
                    result.extend(kept)
                else:
                    # No error lines in the dump; keep a single pointer so
                    # the reader knows a log block was elided.
                    result.append(f"... [{len(block)} log lines elided]")
            else:
                result.extend(block)
            block.clear()

        for line in lines:
            block.append(line)
            if line.strip() == "":
                flush_block()
                result.append(line)
        flush_block()
        return "\n".join(result)

    @classmethod
    def _truncate_pasted_files(cls, text: str) -> str:
        """Truncate pasted file contents to a 50-line cap with a pointer.

        Detects file-fence markers and truncates the fenced content. We also
        catch long *unfenced* runs (heuristic: 50+ consecutive lines that look
        like code, i.e. indented or ending in ``;``/``:``) but only when we
        can infer a path from a preceding fence marker.
        """
        lines = text.split("\n")
        result: list[str] = []
        current_path: str | None = None
        in_fence = False
        fence_line_count = 0
        truncated_count = 0

        for i, line in enumerate(lines):
            fence_match = cls._FILE_FENCE_RE.match(line)
            if fence_match:
                # Close any open fence first.
                if in_fence:
                    if truncated_count > 0:
                        result.append(
                            f"... [truncated {truncated_count} lines, "
                            f"see file at {current_path}]"
                        )
                    in_fence = False
                current_path = (fence_match.group("path") or "").strip().rstrip("`-")
                in_fence = True
                fence_line_count = 0
                truncated_count = 0
                result.append(line)
                continue

            if in_fence:
                # Check for closing fence.
                if line.strip().startswith("```") or (
                    line.strip().startswith("---") and line.strip() == "---"
                ):
                    if truncated_count > 0:
                        result.append(
                            f"... [truncated {truncated_count} lines, "
                            f"see file at {current_path}]"
                        )
                    in_fence = False
                    current_path = None
                    result.append(line)
                    continue
                fence_line_count += 1
                if fence_line_count <= _PASTED_FILE_LINE_CAP:
                    result.append(line)
                else:
                    truncated_count += 1
                continue

            result.append(line)

        # Handle EOF while still in a fence.
        if in_fence and truncated_count > 0:
            result.append(
                f"... [truncated {truncated_count} lines, see file at {current_path}]"
            )

        return "\n".join(result)

    @classmethod
    def _dedup_repeated_blocks(cls, text: str) -> str:
        """Collapse 3+ identical adjacent lines to one + ``[repeated N times]``."""
        lines = text.split("\n")
        if len(lines) < _REPEAT_THRESHOLD:
            return text

        result: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            # Count how many identical lines follow.
            run = 1
            while i + run < len(lines) and lines[i + run] == line:
                run += 1
            if run >= _REPEAT_THRESHOLD:
                result.append(line)
                result.append(f"[repeated {run} times]")
            else:
                result.extend(lines[i : i + run])
            i += run
        return "\n".join(result)


def minimize_prompt(text: str, job_class: str) -> str:
    """Module-level shortcut for :meth:`PromptMinimizer.minimize`."""
    return PromptMinimizer.minimize(text, job_class)


# ---------------------------------------------------------------------------
# Lever 2 — ContextHarness
# ---------------------------------------------------------------------------


class ContextHarness:
    """Pull relevant prior dispatch receipts from the orchestrator state DB.

    Conservative by design: it queries *only* the orchestrator state SQLite
    database for prior dispatch receipts on the same project. It never
    reads external files, code, or unstructured notes. Returns the last 3
    receipts trimmed to four key fields:

    - ``worker_id`` — who did the work
    - ``job_class`` — what kind of work
    - ``outcome`` — success/failure (from ``success`` column)
    - ``key_output`` — trimmed ``output_summary``

    If the DB is missing, unreadable, or has no matching receipts, returns an
    empty string so the caller can dispatch without harness context.
    """

    @staticmethod
    def harness(project_root: str, job_class: str, query: str) -> str:
        """Return a compact harness string of the last 3 receipts for the project.

        ``query`` is accepted for API symmetry but not currently used to
        filter receipts (the harness is project-scoped, not query-scoped).
        Returns ``""`` when there is nothing to harness.
        """
        if not project_root:
            return ""

        db_path = os.path.join(project_root, _STATE_DB_REL)
        if not os.path.isfile(db_path):
            return ""

        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT r.worker_id, r.job_class, r.success, r.output_summary,
                           r.created_at
                    FROM receipts r
                    JOIN dispatches d ON r.dispatch_id = d.id
                    JOIN intents i ON d.intent_id = i.id
                    JOIN projects p ON i.project_id = p.id
                    WHERE p.root_path = ?
                    ORDER BY COALESCE(r.created_at, '') DESC
                    LIMIT 3
                    """,
                    (project_root,),
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return ""

        if not rows:
            return ""

        excerpts: list[str] = []
        for idx, row in enumerate(rows, start=1):
            worker = str(row["worker_id"] or "unknown")
            jc = str(row["job_class"] or "unknown")
            outcome = "success" if row["success"] else "failure"
            key_output = str(row["output_summary"] or "").strip()
            # Trim key_output to a reasonable excerpt (first ~300 chars / 4 lines).
            key_output = cls_trim(key_output, max_chars=300, max_lines=4)
            excerpts.append(
                f"[{idx}] worker={worker} job_class={jc} outcome={outcome}\n"
                f"    key_output: {key_output}"
            )

        header = f"Prior dispatch receipts for project {project_root} (last 3):"
        return header + "\n" + "\n".join(excerpts)


def cls_trim(text: str, max_chars: int = 300, max_lines: int = 4) -> str:
    """Trim a string to ``max_lines`` lines and ``max_chars`` characters."""
    if not text:
        return ""
    lines = text.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append("...")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "..."
    return out


def harness_context(project_root: str, job_class: str, query: str) -> str:
    """Module-level shortcut for :meth:`ContextHarness.harness`."""
    return ContextHarness.harness(project_root, job_class, query)


# ---------------------------------------------------------------------------
# Lever 3 — OutputDirective
# ---------------------------------------------------------------------------


class OutputDirective:
    """Per-job-class ~15-token output directives.

    Each directive is a tight instruction that tells the model *how* to
    answer for its job class — no preamble, no recap. Directives cover all
    14 canonical job classes plus a sensible default.
    """

    _DIRECTIVES: dict[str, str] = {
        "routing_triage": "Answer directly. One sentence.",
        "bulk_extraction": "Return extracted data only. No commentary.",
        "simple_coding": "Return the code. No explanation unless asked.",
        "repo_coding": "Write the code. No preamble, no explanation unless asked.",
        "agentic_repair": "Apply the fix. State what changed in one line.",
        "deep_debugging": "State the root cause, then the fix. Evidence first.",
        "architecture": "Provide the design. Key decisions, risks, and trade-offs.",
        "long_context_synthesis": "Synthesize the answer. Cite sources inline.",
        "schema_validation": "Report PASS or FAIL with the failing field only.",
        "security_review": "List findings by severity. Evidence per finding.",
        "ocr_document_intake": "Return extracted text only. No commentary.",
        "visual_reasoning": "Describe what you see. Be specific and concise.",
        "business_sensitive": "State the decision and the rationale. No filler.",
        "public_content": "Deliver the content. Match the requested voice.",
    }

    _DEFAULT_DIRECTIVE = "Answer directly. No preamble, no recap."

    @classmethod
    def get(cls, job_class: str) -> str:
        """Return the output directive for ``job_class``."""
        return cls._DIRECTIVES.get(job_class, cls._DEFAULT_DIRECTIVE)


def get_output_directive(job_class: str) -> str:
    """Module-level shortcut for :meth:`OutputDirective.get`."""
    return OutputDirective.get(job_class)


# ---------------------------------------------------------------------------
# Lever 4 — EffortCalibrator
# ---------------------------------------------------------------------------


class EffortCalibrator:
    """Map job classes to reasoning effort and max output tokens.

    Guidance comes from ``efficiency_policy.json``'s ``context_budgets_tokens``:
    smaller budgets → lower effort and tighter output caps; larger budgets
    → higher effort and looser/uncapped output. Falls back to a sensible
    medium default for any unknown job class.
    """

    # Explicit calibration table. Keys are job classes; values are
    # (reasoning_effort, max_output_tokens). ``None`` means no cap.
    _CALIBRATION: dict[str, tuple[str, int | None]] = {
        "routing_triage": ("low", 512),
        "simple_coding": ("low", 2048),
        "bulk_extraction": ("low", 2048),
        "schema_validation": ("low", 1024),
        "ocr_document_intake": ("low", 2048),
        "repo_coding": ("medium", 4096),
        "agentic_repair": ("medium", 4096),
        "visual_reasoning": ("medium", 4096),
        "public_content": ("medium", 4096),
        "deep_debugging": ("high", 8192),
        "security_review": ("high", 8192),
        "business_sensitive": ("high", 4096),
        "architecture": ("high", None),
        "long_context_synthesis": ("high", None),
    }

    _DEFAULT = ("medium", 4096)

    @classmethod
    def calibrate(cls, job_class: str, project_root: str | None = None) -> dict[str, Any]:
        """Return ``{"reasoning_effort": str, "max_output_tokens": int | None}``.

        The explicit calibration table is authoritative. If a job class is
        not in the table but *is* in ``efficiency_policy.json``, we infer
        effort from the context budget (small < 10k → low, < 60k → medium,
        else high) as a graceful fallback. Unknown classes get the default.
        """
        if job_class in cls._CALIBRATION:
            effort, max_tokens = cls._CALIBRATION[job_class]
            return {
                "reasoning_effort": effort,
                "max_output_tokens": max_tokens,
            }

        # Fallback: infer from efficiency_policy.json context budget.
        policy = _load_efficiency_policy(project_root)
        budgets = policy.get("context_budgets_tokens", {})
        budget = budgets.get(job_class) or budgets.get("default")
        if isinstance(budget, (int, float)) and budget > 0:
            if budget < 10_000:
                effort, max_tokens = "low", 1024
            elif budget < 60_000:
                effort, max_tokens = "medium", 4096
            else:
                effort, max_tokens = "high", None
            return {"reasoning_effort": effort, "max_output_tokens": max_tokens}

        effort, max_tokens = cls._DEFAULT
        return {
            "reasoning_effort": effort,
            "max_output_tokens": max_tokens,
        }


def calibrate_effort(job_class: str) -> dict[str, Any]:
    """Module-level shortcut for :meth:`EffortCalibrator.calibrate`.

    Uses the explicit calibration table; the ``project_root``-based policy
    fallback is not invoked at the module level (no project context).
    """
    return EffortCalibrator.calibrate(job_class)


# ---------------------------------------------------------------------------
# Lever 5 — WasteDetector
# ---------------------------------------------------------------------------


class WasteDetector:
    """Score a model output for post-dispatch waste.

    Detects four waste modes:

    - **preamble** — output opens with filler ("Sure!", "I'll", "Let me",
      "Here's") before actual content.
    - **recap** — output repeats the question back.
    - **over_formatting** — excessive markdown headers for a simple answer.
    - **ceremony** — hedging phrases ("As requested", "To answer your question").

    Returns a ``conservation_score`` in [0.0, 1.0] where 1.0 = zero waste and
    0.0 = all waste, plus the list of detected modes and notes.
    """

    # Preamble openers. Matched case-insensitively at the start of the output
    # (after stripping leading whitespace), optionally followed by a comma
    # or period, and then the *real* content begins.
    _PREAMBLE_OPENERS: tuple[str, ...] = (
        "sure!",
        "sure,",
        "i'll",
        "let me",
        "here's",
        "here is",
        "of course",
        "certainly",
        "absolutely",
        "great question",
    )

    # Ceremony phrases anywhere in the first ~200 chars.
    _CEREMONY_PHRASES: tuple[str, ...] = (
        "as requested",
        "as you requested",
        "to answer your question",
        "to address your question",
        "to respond to your question",
        "as per your request",
        "per your request",
        "i'd be happy to",
        "i would be happy to",
        "let me help you with that",
    )

    # Words that, if they appear in the output AND the question, suggest a recap.
    # We only flag recap for non-trivial overlap (handled in _detect_recap).
    _RECAP_MIN_WORD_OVERLAP = 6

    # Thresholds for over-formatting. A "simple" answer is short; if it has
    # many markdown headers relative to its length, that's waste.
    _OVER_FORMAT_HEADER_RATIO = 0.15  # >15% of lines are headers
    _OVER_FORMAT_MIN_HEADERS = 3  # at least 3 headers to flag

    @classmethod
    def detect(cls, output: str, job_class: str) -> dict[str, Any]:
        """Return waste analysis for ``output`` given ``job_class``."""
        if not output or not output.strip():
            return {
                "conservation_score": 1.0,
                "waste_modes": [],
                "notes": "Empty output; no waste to detect.",
            }

        modes: list[str] = []
        notes: list[str] = []
        text = output.strip()
        lower = text.lower()

        # --- Preamble ---
        preamble_hit = cls._detect_preamble(lower)
        if preamble_hit:
            modes.append("preamble")
            notes.append(f"Opens with filler phrase '{preamble_hit}'.")

        # --- Ceremony ---
        ceremony_hit = cls._detect_ceremony(lower)
        if ceremony_hit:
            modes.append("ceremony")
            notes.append(f"Ceremony phrase '{ceremony_hit}' detected.")

        # --- Over-formatting ---
        if cls._detect_over_formatting(text):
            modes.append("over_formatting")
            notes.append("Excessive markdown headers for a short answer.")

        # --- Recap (requires the question — we can't detect without it, so
        # we use a heuristic: the output's first sentence restates a question
        # ending in '?'. We flag rhetorical restatements.)
        if cls._detect_recap_heuristic(text):
            modes.append("recap")
            notes.append("Output appears to restate the question before answering.")

        # --- Score ---
        # Each detected mode reduces the score. With 4 possible modes, each
        # mode costs 0.25. We also weight preamble and ceremony slightly
        # higher (they're pure waste) and over-formatting/recap slightly
        # lower (sometimes contextually justified).
        weights = {
            "preamble": 0.30,
            "ceremony": 0.25,
            "over_formatting": 0.20,
            "recap": 0.25,
        }
        penalty = sum(weights.get(m, 0.20) for m in modes)
        score = max(0.0, 1.0 - penalty)

        # Round to 2 decimals for readability.
        score = round(score, 2)

        return {
            "conservation_score": score,
            "waste_modes": modes,
            "notes": " ".join(notes) if notes else "No waste detected.",
        }

    # -- internal detectors ------------------------------------------------

    @classmethod
    def _detect_preamble(cls, lower_text: str) -> str | None:
        """Return the preamble opener if the text starts with one, else None."""
        first_chunk = lower_text[:60]
        for opener in cls._PREAMBLE_OPENERS:
            if first_chunk.startswith(opener):
                return opener
        return None

    @classmethod
    def _detect_ceremony(cls, lower_text: str) -> str | None:
        """Return the ceremony phrase if found near the start, else None."""
        head = lower_text[:300]
        for phrase in cls._CEREMONY_PHRASES:
            if phrase in head:
                return phrase
        return None

    @classmethod
    def _detect_over_formatting(cls, text: str) -> bool:
        """Flag excessive markdown headers relative to output length."""
        lines = text.split("\n")
        non_blank = [ln for ln in lines if ln.strip()]
        if not non_blank:
            return False
        # Markdown headers: lines starting with # or ## etc.
        header_count = sum(1 for ln in non_blank if ln.lstrip().startswith("#"))
        if header_count < cls._OVER_FORMAT_MIN_HEADERS:
            return False
        ratio = header_count / len(non_blank)
        # Only flag if the answer is relatively short (under ~40 non-blank lines)
        # AND the header ratio is high. Long documents legitimately use headers.
        if len(non_blank) < 40 and ratio > cls._OVER_FORMAT_HEADER_RATIO:
            return True
        return False

    @staticmethod
    def _detect_recap_heuristic(text: str) -> bool:
        """Heuristic: output opens by restating a question (ends in '?').

        Flags when the first 2 sentences contain a question mark that isn't
        part of the actual answer — i.e., the model echoes the prompt's
        question before answering. This is a conservative heuristic: it only
        fires when there's a question mark in the first ~150 chars followed
        by the real answer.
        """
        head = text[:200]
        # Find sentences in the head.
        sentences = re.split(r"(?<=[.!?])\s+", head)
        if len(sentences) < 2:
            return False
        # If the first sentence ends in '?' and a later sentence doesn't,
        # the first is likely a restated question.
        first = sentences[0].strip()
        if first.endswith("?") and len(first) < 150:
            # Make sure there's actual content after.
            rest = " ".join(sentences[1:]).strip()
            if len(rest) > 20:
                return True
        return False


def detect_waste(output: str, job_class: str) -> dict[str, Any]:
    """Module-level shortcut for :meth:`WasteDetector.detect`."""
    return WasteDetector.detect(output, job_class)
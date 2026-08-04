"""Conservation report — the dispatch preparation and analysis entrypoints.

This module wires the five conservation levers together into two cohesive
operations used by the orchestrator's dispatch pipeline:

- :func:`prepare_dispatch` — runs levers 1-4 *before* the model is called,
  shrinking the input and setting effort/output caps. Demand-side prevention.
- :func:`analyze_dispatch` — runs lever 5 *after* the model responds,
  scoring the output for waste. Post-dispatch audit.

Usage::

    from orchestrator.usage.conservation_report import prepare_dispatch, analyze_dispatch

    pre = prepare_dispatch(raw_text, job_class="repo_coding", project_root=root)
    # ... dispatch with pre["minimized_prompt"], pre["effort"], etc. ...
    post = analyze_dispatch(model_output, job_class="repo_coding")

The module imports cleanly even without a project root or state DB — all
external lookups degrade gracefully to empty strings and defaults.
"""

from __future__ import annotations

from typing import Any

from orchestrator.usage.conservation import (
    PromptMinimizer,
    ContextHarness,
    OutputDirective,
    EffortCalibrator,
    WasteDetector,
)

__all__ = ["prepare_dispatch", "analyze_dispatch"]


def prepare_dispatch(
    raw_text: str,
    job_class: str,
    project_root: str | None,
) -> dict[str, Any]:
    """Run the four pre-dispatch conservation levers and return a dispatch plan.

    Parameters
    ----------
    raw_text:
        The full, unminimized prompt text as composed by the caller.
    job_class:
        One of the 14 canonical job classes (e.g. ``"repo_coding"``).
    project_root:
        Absolute path to the project root, or ``None`` if dispatch is not
        project-scoped. Used only for the context harness; passing ``None``
        skips harnessing and returns an empty string for that field.

    Returns
    -------
    dict with keys:
        - ``minimized_prompt`` (str) — lever 1 output
        - ``context_harness`` (str) — lever 2 output (empty if none)
        - ``output_directive`` (str) — lever 3 output
        - ``effort`` (dict) — lever 4 output
        - ``original_length`` (int) — character length of ``raw_text``
        - ``minimized_length`` (int) — character length of minimized prompt
    """
    original_length = len(raw_text or "")

    # Lever 1 — minimize the prompt (strips logs, truncates pasted files,
    # dedupes blocks, preserves the user's question verbatim).
    minimized_prompt = PromptMinimizer.minimize(raw_text, job_class)

    # Lever 2 — harness prior dispatch context from the state DB.
    context_harness = ""
    if project_root:
        context_harness = ContextHarness.harness(project_root, job_class, raw_text)

    # Lever 3 — per-job-class output directive.
    output_directive = OutputDirective.get(job_class)

    # Lever 4 — effort calibration (reasoning_effort + max_output_tokens).
    effort = EffortCalibrator.calibrate(job_class, project_root)

    minimized_length = len(minimized_prompt or "")

    return {
        "minimized_prompt": minimized_prompt,
        "context_harness": context_harness,
        "output_directive": output_directive,
        "effort": effort,
        "original_length": original_length,
        "minimized_length": minimized_length,
    }


def analyze_dispatch(output: str, job_class: str) -> dict[str, Any]:
    """Run the post-dispatch waste detector (lever 5) on the model's output.

    Parameters
    ----------
    output:
        The raw model output text.
    job_class:
        The job class the dispatch was run under.

    Returns
    -------
    dict with keys:
        - ``conservation_score`` (float) — 1.0 = zero waste, 0.0 = all waste
        - ``waste_modes`` (list[str]) — detected waste modes
        - ``notes`` (str) — human-readable explanation
    """
    return WasteDetector.detect(output, job_class)
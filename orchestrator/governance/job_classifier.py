"""Job classifier — maps intent text to job class.

Uses keyword matching + capability requirements to classify intents into job classes.
Job classes determine which workers are eligible and what approval/budget rules apply.

Job Classes (from governor v2):
- repo_coding: Code generation, refactoring, bug fixes in repositories
- agentic_repair: Multi-step debugging, test-driven repair loops
- bulk_extraction: Data extraction from multiple files/APIs
- classification: Categorization, tagging, sentiment analysis
- summarization: Document/text summarization
- translation: Language translation
- code_review: PR review, security scanning, quality gates
- data_science: Analysis, visualization, notebook work
- documentation: Docs, comments, README generation
- brainstorming: Ideation, planning, exploration
- quick_question: Factual Q&A, lookups
- creative_writing: Stories, marketing copy, content generation
- technical_analysis: Architecture, system design, RFCs
- data_pipeline: ETL, data transformation, batch processing

Author: Hermes Agent
Date: 2026-06-10
Phase: 2 (Routing Integration)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ClassificationResult:
    job_class: str
    confidence: float  # 0.0-1.0
    matched_keywords: list[str]
    reasoning: str


# Keyword patterns for each job class
# Higher weight = stronger signal
JOB_CLASS_PATTERNS: dict[str, list[tuple[str, float]]] = {
    "repo_coding": [
        (r"\b(fix|bug|error|exception|crash|fail)\b", 0.8),
        (r"\b(refactor|clean up|restructure)\b", 0.7),
        (r"\b(implement|add|create|write)\b.*\b(code|function|method|class)\b", 0.7),
        (r"\b(test|unittest|pytest)\b", 0.6),
        (r"\b(\.py|\.js|\.ts|\.rs|\.go|\.java)\b", 0.5),
    ],
    "agentic_repair": [
        (r"\b(debug|troubleshoot|diagnose|investigate)\b", 0.8),
        (r"\b(root cause|why is|what's wrong)\b", 0.7),
        (r"\b(fix.*test|test.*fail)\b", 0.6),
        (r"\b(iterative|loop|retry)\b", 0.5),
    ],
    "bulk_extraction": [
        (r"\b(extract|scrape|pull|fetch)\b.*\b(data|info|text)\b", 0.8),
        (r"\b(multiple|all|batch|bulk)\b", 0.6),
        (r"\b(API|endpoint|HTTP|request)\b", 0.5),
        (r"\b(parse|deserialize)\b", 0.5),
    ],
    "classification": [
        (r"\b(classify|categorize|tag|label)\b", 0.9),
        (r"\b(sentiment|topic|intent)\b", 0.7),
        (r"\b(sort|group|organize)\b", 0.5),
    ],
    "summarization": [
        (r"\b(summarize|summary|tl;dr|overview)\b", 0.9),
        (r"\b(key points|highlights|brief)\b", 0.7),
        (r"\b(condense|boil down)\b", 0.6),
    ],
    "translation": [
        (r"\b(translate|translation)\b", 0.9),
        (r"\b(from.*to|into.*language)\b", 0.7),
        (r"\b(localize|localization)\b", 0.6),
    ],
    "code_review": [
        (r"\b(review|audit|inspect)\b.*\b(code|PR|pull request)\b", 0.8),
        (r"\b(security|vulnerability|CVE)\b", 0.7),
        (r"\b(best practice|anti-pattern|code quality)\b", 0.6),
        (r"\b(lint|style|format)\b", 0.5),
    ],
    "data_science": [
        (r"\b(analyze|analysis|explore)\b.*\b(data|dataset)\b", 0.8),
        (r"\b(visualization|plot|chart|graph)\b", 0.7),
        (r"\b(pandas|numpy|matplotlib|seaborn)\b", 0.6),
        (r"\b(statistics|correlation|regression)\b", 0.6),
    ],
    "documentation": [
        (r"\b(document|docstring|comment)\b", 0.8),
        (r"\b(README|docs|documentation)\b", 0.7),
        (r"\b(explain|describe|write up)\b", 0.5),
    ],
    "brainstorming": [
        (r"\b(brainstorm|ideate|explore|consider)\b", 0.8),
        (r"\b(what if|how might|possibilities)\b", 0.7),
        (r"\b(options|alternatives|approaches)\b", 0.6),
    ],
    "quick_question": [
        (r"\b(what is|who is|when|where|how many)\b", 0.7),
        (r"\b(define|explain.*concept)\b", 0.6),
        (r"\b(fact|trivia)\b", 0.5),
    ],
    "creative_writing": [
        (r"\b(write|compose|draft)\b.*\b(story|email|post|copy|content)\b", 0.8),
        (r"\b(creative|engaging|persuasive)\b", 0.6),
        (r"\b(marketing|social|blog)\b", 0.5),
    ],
    "technical_analysis": [
        (r"\b(architecture|design|system)\b", 0.8),
        (r"\b(RFC|proposal|spec)\b", 0.7),
        (r"\b(scalability|performance|bottleneck)\b", 0.6),
        (r"\b(trade-off|tradeoff)\b", 0.6),
    ],
    "data_pipeline": [
        (r"\b(pipeline|ETL|transform|load)\b", 0.8),
        (r"\b(batch|scheduled|cron)\b", 0.6),
        (r"\b(ingest|export|migrate)\b", 0.6),
    ],
}

# Default job class when no strong match
DEFAULT_JOB_CLASS = "quick_question"

# Minimum confidence threshold to assign a non-default job class
MIN_CONFIDENCE = 0.3


def classify_intent(text: str) -> ClassificationResult:
    """Classify intent text into a job class.

    Args:
        text: Raw intent text from user

    Returns:
        ClassificationResult with job_class, confidence, matched keywords, reasoning
    """
    text_lower = text.lower()

    scores: dict[str, float] = {}
    matched: dict[str, list[str]] = {}

    for job_class, patterns in JOB_CLASS_PATTERNS.items():
        class_score = 0.0
        class_matches = []

        for pattern, weight in patterns:
            if re.search(pattern, text_lower):
                class_score += weight
                class_matches.append(pattern)

        if class_score > 0:
            # Normalize: cap at 1.0, use sigmoid-like scaling
            normalized = min(1.0, class_score / 2.0)
            scores[job_class] = normalized
            matched[job_class] = class_matches

    if not scores:
        return ClassificationResult(
            job_class=DEFAULT_JOB_CLASS,
            confidence=0.5,
            matched_keywords=[],
            reasoning=f"No strong keywords matched; defaulting to {DEFAULT_JOB_CLASS}",
        )

    # Pick highest scoring job class
    best_class = max(scores.keys(), key=lambda k: scores[k])
    best_confidence = scores[best_class]
    best_matches = matched[best_class]

    # Check if confidence meets threshold
    if best_confidence < MIN_CONFIDENCE:
        return ClassificationResult(
            job_class=DEFAULT_JOB_CLASS,
            confidence=best_confidence,
            matched_keywords=best_matches,
            reasoning=f"Best match '{best_class}' had low confidence ({best_confidence:.2f}); using {DEFAULT_JOB_CLASS}",
        )

    reasoning = f"Matched {len(best_matches)} keyword patterns for '{best_class}' with confidence {best_confidence:.2f}"

    return ClassificationResult(
        job_class=best_class,
        confidence=best_confidence,
        matched_keywords=best_matches,
        reasoning=reasoning,
    )


def classify_with_fallback(text: str, override: str | None = None) -> dict[str, Any]:
    """Classify intent with optional override.

    Args:
        text: Raw intent text
        override: Optional job class override (bypasses classification)

    Returns:
        Dict with job_class, confidence, metadata
    """
    if override:
        return {
            "job_class": override,
            "confidence": 1.0,
            "matched_keywords": ["user_override"],
            "reasoning": f"User explicitly requested job class: {override}",
            "overridden": True,
        }

    result = classify_intent(text)
    return {
        "job_class": result.job_class,
        "confidence": result.confidence,
        "matched_keywords": result.matched_keywords,
        "reasoning": result.reasoning,
        "overridden": False,
    }

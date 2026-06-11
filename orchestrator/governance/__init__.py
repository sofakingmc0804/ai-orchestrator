"""Governance module for AI Orchestrator.

This module provides workforce modeling, job classification, and budget probing.
Merged from .ai-resource-governor during consolidation (2026-06-10).

Components:
- worker_roster_builder: Build worker cards from adapter contracts + live probes
- worker_selector: Select best worker for a job class
- budget_probes: Live quota readback per provider
- job_classifier: Map intent text → job class

Usage:
    from orchestrator.governance import worker_selector
    worker = worker_selector.select_worker("repo_coding", intent_text)
"""

from orchestrator.governance.worker_roster_builder import build_worker_roster
from orchestrator.governance.worker_selector import select_worker

__all__ = ["build_worker_roster", "select_worker"]

from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_MARKERS = {".git", "AGENTS.md", "CLAUDE.md", "package.json", "pyproject.toml", "requirements.txt"}


def project_id_for(path: Path) -> str:
    digest = hashlib.sha1(str(path).lower().encode("utf-8")).hexdigest()[:10]
    return "proj_" + "".join(ch.lower() if ch.isalnum() else "_" for ch in path.name)[:40] + "_" + digest


def discover_projects(roots: list[Path], max_depth: int = 4) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for root in roots:
        if not root.exists():
            continue
        root = root.resolve()
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                children = list(current.iterdir())
            except (OSError, PermissionError):
                continue
            names = {c.name for c in children}
            if names & PROJECT_MARKERS:
                policy_file = current / ".orchestrator-policy.yaml"
                found[str(current)] = {
                    "id": project_id_for(current),
                    "name": current.name or str(current),
                    "root_path": str(current),
                    "domain": "coding" if {"package.json", "pyproject.toml", ".git"} & names else "general",
                    "consequence_tier": "medium" if "D:\\SharedRoot" in str(current) else "low",
                    "policy_file_path": str(policy_file) if policy_file.exists() else "",
                }
            if depth < max_depth:
                for child in children:
                    if child.is_dir() and child.name not in {"node_modules", ".venv", "__pycache__", ".git"}:
                        stack.append((child, depth + 1))
    return list(found.values())

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path
from typing import Any, Iterator

import yaml


TASK_LEDGER_NAMES = {"tasks.yaml", "tasks.yml"}
MARKDOWN_BACKLOG_NAMES = {"todo.md", "backlog.md", "plan.md"}
SOURCE_GLOBS = ("TASKS.yaml", "TASKS.yml", "TODO.md", "BACKLOG.md", "PLAN.md")
ACTIONABLE_STATUSES = {"ready", "active", "blocked", "queued", "in_progress", "in-progress"}
SKIP_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
MAX_DEPTH = 5
MAX_SOURCE_FILES = 250
MAX_SCAN_DIRECTORIES = 1_200


def _source_files(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    rg = shutil.which("rg")
    if rg:
        args = [rg, "--files", "--no-messages"]
        for name in SOURCE_GLOBS:
            args.extend(["--iglob", name])
        for name in SKIP_DIRECTORIES:
            args.extend(["--glob", f"!**/{name}/**"])
        args.append(str(root))
        try:
            completed = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired):
            # A broad shared-drive scan must stay bounded; falling back to a
            # directory walk here can turn one slow root into an unbounded run.
            return
        if completed.returncode in {0, 1}:
            paths: list[Path] = []
            for value in completed.stdout.splitlines():
                path = Path(value)
                if not path.is_absolute():
                    path = root / path
                if path.is_file():
                    paths.append(path)
            for path in sorted(paths, key=lambda value: str(value).lower())[:MAX_SOURCE_FILES]:
                yield path
            return
        return

    stack: deque[tuple[Path, int]] = deque([(root, 0)])
    yielded = 0
    scanned_directories = 0
    while stack and yielded < MAX_SOURCE_FILES and scanned_directories < MAX_SCAN_DIRECTORIES:
        current, depth = stack.popleft()
        scanned_directories += 1
        try:
            children = sorted(current.iterdir(), key=lambda path: path.name.lower(), reverse=True)
        except OSError:
            continue
        for path in children:
            name = path.name.lower()
            if path.is_file() and name in TASK_LEDGER_NAMES | MARKDOWN_BACKLOG_NAMES:
                yielded += 1
                yield path
                if yielded >= MAX_SOURCE_FILES:
                    return
            elif path.is_dir() and depth < MAX_DEPTH and name not in SKIP_DIRECTORIES:
                stack.append((path, depth + 1))


def _fingerprint(*parts: object) -> str:
    joined = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _line_for(text: str, pattern: str) -> int | None:
    for number, line in enumerate(text.splitlines(), start=1):
        if re.search(pattern, line):
            return number
    return None


def _candidate(
    *,
    kind: str,
    source_path: Path,
    source_line: int | None,
    source_task_id: str | None,
    title: str,
    description: str,
    source_status: str,
    priority: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    project_root = source_path.parent
    fingerprint = _fingerprint(source_path, source_task_id or source_line or title, title, description, source_status, kind)
    return {
        "fingerprint": fingerprint,
        "kind": kind,
        "source_path": str(source_path),
        "source_line": source_line,
        "source_task_id": source_task_id,
        "project_root": str(project_root),
        "title": title,
        "description": description,
        "source_status": source_status,
        "priority": priority,
        "state": "discovered",
        "error": error,
        "payload": {
            "source_path": str(source_path),
            "source_line": source_line,
            "source_task_id": source_task_id,
            "source_status": source_status,
        },
    }


def _walk_task_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if any(key in value for key in ("id", "title", "name")) and any(key in value for key in ("status", "state")):
            yield value
        for child in value.values():
            yield from _walk_task_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_task_dicts(child)


def _discover_yaml_ledger(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = int(mark.line) + 1 if mark is not None else None
        description = f"YAML parsing error at line {line if line is not None else 'unknown'}: {str(exc).splitlines()[0]}"
        return [
            _candidate(
                kind="malformed_task_ledger",
                source_path=path,
                source_line=line,
                source_task_id=None,
                title=f"Repair malformed task ledger: {path.name}",
                description=description,
                source_status="invalid",
                error=description,
            )
        ]
    candidates: list[dict[str, Any]] = []
    for task in _walk_task_dicts(payload):
        status = str(task.get("status") or task.get("state") or "").strip().lower()
        if status not in ACTIONABLE_STATUSES:
            continue
        task_id = str(task.get("id") or task.get("task") or "").strip() or None
        title = str(task.get("title") or task.get("name") or task_id or "Untitled task").strip()
        description = str(task.get("description") or task.get("next_action") or title).strip()
        line = _line_for(text, rf"^\s*-?\s*id:\s*{re.escape(task_id)}\s*$") if task_id else _line_for(text, re.escape(title))
        candidates.append(
            _candidate(
                kind="task_ledger",
                source_path=path,
                source_line=line,
                source_task_id=task_id,
                title=title,
                description=description,
                source_status=status,
                priority=str(task.get("priority") or "").strip() or None,
            )
        )
    return candidates


def _discover_markdown_backlog(path: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), start=1):
        match = re.match(r"^\s*[-*]\s*\[ \]\s+(?P<title>.+?)\s*$", line)
        if not match:
            continue
        title = match.group("title").strip()
        candidates.append(
            _candidate(
                kind="markdown_backlog",
                source_path=path,
                source_line=line_number,
                source_task_id=f"line-{line_number}",
                title=title,
                description=title,
                source_status="ready",
            )
        )
    return candidates


def discover_backlog_candidates(roots: list[Path]) -> list[dict[str, Any]]:
    """Discover explicit unfinished work from bounded, owner-authorized project roots."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        for path in _source_files(root.expanduser()):
            path_key = str(path.resolve()).lower()
            if path_key in seen:
                continue
            seen.add(path_key)
            if path.name.lower() in TASK_LEDGER_NAMES:
                candidates.extend(_discover_yaml_ledger(path))
            else:
                candidates.extend(_discover_markdown_backlog(path))
    return candidates

from __future__ import annotations

import sys
from pathlib import Path


MANAGED_REQUIREMENTS = Path("C:/ProgramData/OpenAI/Codex/requirements.toml")


def _managed_gate_installed() -> bool:
    if not MANAGED_REQUIREMENTS.exists():
        return False
    try:
        text = MANAGED_REQUIREMENTS.read_text(encoding="utf-8")
    except OSError:
        return False
    return "skill_gate.py" in text and "[hooks]" in text


if _managed_gate_installed():
    print("{}")
    raise SystemExit(0)


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.skills.runtime import main


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path

from orchestrator.cli import aliases


ROOT = Path(__file__).resolve().parents[1]


def test_powershell_alias_detection_wins_over_comspec(monkeypatch) -> None:
    monkeypatch.delenv("SHELL", raising=False)
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setenv("PSModulePath", r"C:\Users\Couch\Documents\PowerShell\Modules;C:\Windows\System32")

    assert aliases.get_shell_type() == "powershell"


def test_alias_installer_uses_console_safe_ascii_output() -> None:
    paths = [
        Path(aliases.__file__),
        ROOT / "scripts" / "migrate-governor-to-orchestrator.py",
    ]

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "✅" not in source
        assert "⚠" not in source
        assert "❌" not in source
        assert "📊" not in source
        assert "💾" not in source
        assert "🔌" not in source
        assert "→" not in source


def test_operational_readiness_checklist_is_tracked_with_proof() -> None:
    readiness = ROOT / "docs" / "OPERATIONAL_READINESS.md"
    migration = ROOT / "docs" / "MIGRATION_COMPLETE.md"

    assert readiness.exists()
    text = readiness.read_text(encoding="utf-8")
    for item in (
        "Run end-to-end tests",
        "Start server, verify UIs",
        "Test dispatch flow end-to-end",
        "Install CLI aliases",
        "Install Windows context menu",
        "Migrate any custom governor scripts to orchestrator CLI",
        "Update cron jobs to use new paths",
        "Deprecate `.ai-resource-governor/` entirely",
        "Add new providers/adapters as needed",
        "Expand benchmark suite",
        "Add more job classes",
    ):
        assert item in text
    assert "status" in text.lower()
    assert "proof" in text.lower()
    assert "consumer" in text.lower()

    migration_text = migration.read_text(encoding="utf-8")
    assert "⏳ Run end-to-end tests" not in migration_text
    assert "See `docs/OPERATIONAL_READINESS.md`" in migration_text

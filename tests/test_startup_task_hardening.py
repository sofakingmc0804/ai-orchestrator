from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_startup_task_installer_avoids_av_trigger_patterns() -> None:
    installer = (ROOT / "scripts" / "install-startup-task.ps1").read_text(encoding="utf-8")
    lower = installer.lower()

    assert "executionpolicy bypass" not in lower
    assert "-executionpolicy" not in lower
    assert "wscript" not in lower
    assert "-noninteractive" in lower
    assert "-windowstyle hidden" in lower
    assert "pythonw.exe" in lower
    assert "no-console-task-launcher.pyw" in lower
    assert "-hidden" in lower


def test_orchestrator_watchdog_launcher_does_not_change_execution_policy() -> None:
    launcher = (ROOT / "scripts" / "start-orchestrator.ps1").read_text(encoding="utf-8")
    lower = launcher.lower()

    assert "executionpolicy" not in lower
    assert "bypass" not in lower
    assert "/api/dashboard-status" in lower
    assert "/api/spec-status" not in lower


def test_no_console_task_launcher_uses_create_no_window() -> None:
    launcher = (ROOT / "scripts" / "no-console-task-launcher.pyw").read_text(encoding="utf-8").lower()

    assert "create_no_window" in launcher
    assert "subprocess.call" in launcher
    assert "os.devnull" in launcher

from __future__ import annotations

from types import SimpleNamespace

import orchestrator.governance.hermes_exec as hermes_exec


def test_governed_hermes_executor_preserves_json_prompt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=b"OK", stderr=b"")

    monkeypatch.setattr(hermes_exec.subprocess, "run", fake_run)
    prompt = '{"terminal_state":"produced","summary":"desktop Hermes runtime"}'

    completed = hermes_exec.run_hermes_command("C:/Hermes/hermes.exe", ["-z", prompt])

    assert completed.returncode == 0
    assert captured["argv"] == ["C:/Hermes/hermes.exe", "-z", prompt]


def test_governed_hermes_shim_uses_python_argument_bridge() -> None:
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    shim = repo_root / ".runtime" / "ai-resource-governor" / "bin" / "hermes.ps1"

    text = shim.read_text(encoding="utf-8")

    assert "orchestrator.governance.hermes_exec" in text
    assert "function ConvertTo-HermesArgvJson" in text
    assert text.count("ConvertTo-HermesArgvJson -Argv @($args)") == 2

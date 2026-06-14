# Codex CLI Stop-hook adapter -> vendor-neutral epistemic engine.
# Codex passes JSON on stdin including 'last_assistant_message' (the assistant's latest message).
# On a block-level violation we emit {"decision":"block","reason":"..."} on stdout, which Codex
# injects back as a forcing follow-up prompt. Fail-open: any error -> allow (exit 0, no output),
# so a hook bug can never freeze a live Codex session.
$ErrorActionPreference = 'Stop'
try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $hook = $raw | ConvertFrom-Json
    $resp = if ($hook.last_assistant_message) { [string]$hook.last_assistant_message } else { '' }
    if ([string]::IsNullOrWhiteSpace($resp)) { exit 0 }

    . (Join-Path $PSScriptRoot '..\engine.ps1')
    $v = Get-EpistemicVerdict -Response $resp
    if ($v.decision -eq 'block') {
        [ordered]@{ decision = 'block'; reason = $v.reason } | ConvertTo-Json -Compress
    }
    exit 0
}
catch {
    exit 0   # fail open: never block a live Codex session on a hook error
}

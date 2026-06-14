# Gemini CLI AfterAgent-hook adapter -> vendor-neutral epistemic engine.
# Gemini passes JSON on stdin with 'prompt_response' (the final assistant prose) and 'prompt'
# (the user text). On a block-level violation we emit {"decision":"deny","reason":"..."} on stdout,
# which Gemini injects back to the agent as a correction prompt. 'stop_hook_active' guards the
# retry loop. Fail-open: any error -> {"decision":"allow"}, so a hook bug never freezes a session.
$ErrorActionPreference = 'Stop'
try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { Write-Output '{"decision":"allow"}'; exit 0 }

    $hook = $raw | ConvertFrom-Json
    if ($hook.stop_hook_active -eq $true) { Write-Output '{"decision":"allow"}'; exit 0 }

    $resp = if ($hook.prompt_response) { [string]$hook.prompt_response } else { '' }
    if ([string]::IsNullOrWhiteSpace($resp)) { Write-Output '{"decision":"allow"}'; exit 0 }
    $prompt = if ($hook.prompt) { [string]$hook.prompt } else { '' }

    . (Join-Path $PSScriptRoot '..\engine.ps1')
    $v = Get-EpistemicVerdict -Response $resp -Prompt $prompt
    if ($v.decision -eq 'block') {
        [ordered]@{
            decision      = 'deny'
            reason        = $v.reason
            systemMessage = 'Epistemic enforcer: recommendation without EPISTEMIC_BLOCK - agent retrying.'
        } | ConvertTo-Json -Compress | Write-Output
    }
    else {
        Write-Output '{"decision":"allow"}'
    }
    exit 0
}
catch {
    Write-Output '{"decision":"allow"}'   # fail open
    exit 0
}

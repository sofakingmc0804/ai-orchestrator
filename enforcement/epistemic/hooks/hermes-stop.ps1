# Hermes (NousResearch hermes-agent) subagent_stop / post-LLM hook adapter -> shared engine.
# Hermes passes a JSON payload on stdin; the assistant-message field name varies by event/version,
# so we probe the likely fields. On a block-level violation we emit {"decision":"block","reason":"..."}
# on stdout, which Hermes injects as a forcing follow-up. Fail-open: any error / no text -> allow (exit 0).
$ErrorActionPreference = 'Stop'
try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $h = $raw | ConvertFrom-Json

    $resp = ''
    foreach ($f in @('assistant_response','last_assistant_message','assistant_message','final_message',
                     'final_response','response','message','content','output','text','completion')) {
        if (($h.PSObject.Properties.Name -contains $f) -and $h.$f) { $resp = [string]$h.$f; break }
    }
    if ([string]::IsNullOrWhiteSpace($resp)) { exit 0 }

    . (Join-Path $PSScriptRoot '..\engine.ps1')
    $v = Get-EpistemicVerdict -Response $resp
    if ($v.decision -eq 'block') {
        [ordered]@{ decision = 'block'; reason = $v.reason } | ConvertTo-Json -Compress
    }
    exit 0
}
catch {
    exit 0   # fail open: never block a live Hermes session on a hook error
}

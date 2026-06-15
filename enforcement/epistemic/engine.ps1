# Retired prose-format enforcement engine.
#
# Consequential reasoning is now enforced by execution/file/source-backed gates,
# not by requiring an agent-authored EPISTEMIC_BLOCK. This function remains only
# so old adapters fail safe and can emit an audit trace.

function Get-EpistemicVerdict {
    param(
        [string]$Response = '',
        [string]$Prompt = ''
    )

    try {
        $auditDir = Join-Path $env:USERPROFILE ".codex\audit"
        if (-not (Test-Path -LiteralPath $auditDir)) {
            New-Item -ItemType Directory -Path $auditDir -Force | Out-Null
        }

        $auditPath = Join-Path $auditDir "retired-epistemic-engine.jsonl"
        $payload = [ordered]@{
            timestamp = (Get-Date).ToString("o")
            decision = "allow"
            reason = "retired_prose_gate"
            response_chars = if ($null -eq $Response) { 0 } else { $Response.Length }
            prompt_chars = if ($null -eq $Prompt) { 0 } else { $Prompt.Length }
        }
        Add-Content -LiteralPath $auditPath -Value ($payload | ConvertTo-Json -Compress) -Encoding UTF8
    } catch {
        # Observability must never become enforcement.
    }

    return [ordered]@{ decision = 'allow'; code = ''; reason = 'retired_prose_gate' }
}

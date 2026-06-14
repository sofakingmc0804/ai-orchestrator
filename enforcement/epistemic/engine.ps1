# Vendor-neutral epistemic enforcement engine (provider-agnostic).
# Dot-source this file, then call Get-EpistemicVerdict -Response <text> [-Prompt <text>].
# Returns an ordered hashtable: @{ decision = 'block'|'allow'; code = 'V1'|''; reason = '<text>' }
#
# Tier (owner-set 2026-06-14): only V1 (a recommendation with NO EPISTEMIC_BLOCK at all)
# returns 'block'. Finer structural checks are advisory and do not block here.
# Fail-open: any error or empty input returns 'allow'. ASCII-only; runs under PS 5.1 and pwsh 7.

function Get-EpistemicVerdict {
    param(
        [string]$Response = '',
        [string]$Prompt = ''
    )

    $allow = [ordered]@{ decision = 'allow'; code = ''; reason = '' }
    if ([string]::IsNullOrWhiteSpace($Response)) { return $allow }

    try {
        $lower = $Response.ToLowerInvariant()

        # C1: code-only response -> skip
        $nonBlank  = @($Response -split "`n" | Where-Object { $_.Trim() -ne '' })
        $codeLines = @($nonBlank | Where-Object { $_.TrimStart().StartsWith('```') -or $_.StartsWith('    ') })
        $codeRatio = if ($nonBlank.Count -gt 0) { [double]$codeLines.Count / $nonBlank.Count } else { 0 }
        if ($codeRatio -ge 0.70) { return $allow }

        $wordCount = @($Response -split '\s+' | Where-Object { $_ -ne '' }).Count

        $recMarkers = @('recommend','should','suggest','best approach','i would','you should',
                        'we should','the best','better to','better approach','optimal',
                        'correct approach','the answer is','the solution is','go with','best practice')
        $hasRec = $false
        foreach ($m in $recMarkers) { if ($lower.Contains($m)) { $hasRec = $true; break } }

        $hasBlock = $lower.Contains('## epistemic_block')

        # C2/C3: trivial or non-recommendation -> nothing to enforce
        if ($wordCount -lt 80 -and -not $hasRec -and -not $hasBlock) { return $allow }
        if (-not $hasRec -and -not $hasBlock) { return $allow }

        # C4: pure clarification (>=60% of sentences are questions) -> skip
        $sentences = @($Response -split '(?<=[.!?])\s+' | Where-Object { $_.Trim() -ne '' })
        if ($sentences.Count -gt 0) {
            $q = @($sentences | Where-Object { $_.TrimEnd().EndsWith('?') })
            if (($q.Count / [Math]::Max($sentences.Count, 1)) -ge 0.6) { return $allow }
        }

        # C5: transformation request -> skip
        if (-not [string]::IsNullOrWhiteSpace($Prompt)) {
            $lp = $Prompt.ToLowerInvariant()
            foreach ($t in @('rewrite','rephrase','summarize','translate','extract','format',
                             'paraphrase','condense','shorten','reformat','convert this')) {
                if ($lp.Contains($t) -and -not $hasRec -and -not $hasBlock) { return $allow }
            }
        }

        # V1 (block-level): recommendation present, no EPISTEMIC_BLOCK
        if ($hasRec -and -not $hasBlock) {
            $reason = @'
EPISTEMIC GATE - STRUCTURE REQUIRED (V1)

Your response makes a recommendation or conclusion but has no EPISTEMIC_BLOCK.
Add this section BEFORE your recommendation, then continue:

## EPISTEMIC_BLOCK
DOMAIN_SCOPE: [OPEN_WORLD|CLOSED_WORLD] - governing scope
CASE_ANCHORS: at least 2 task-specific anchors from the request
CLAIM: what you are recommending, one sentence
INVERSE_CLAIM: the opposing position, directly inverse to CLAIM
FALSIFICATION: why the INVERSE_CLAIM does not fully apply here
CONDITIONS_FOR_INVERSE: when / if / unless the inverse becomes correct
EVIDENCE_BASIS: grounded in the actual task, file, standard, or constraint
CONFIDENCE: [HIGH|MEDIUM|LOW] - one sentence justification

Closed-world facts (security requirements, RFC/protocol compliance, type or
hardware limits) are exempt only when DOMAIN_SCOPE: CLOSED_WORLD is stated.
'@
            return [ordered]@{ decision = 'block'; code = 'V1'; reason = $reason }
        }

        # Block present (or no recommendation): finer checks are advisory in this tier.
        return $allow
    }
    catch {
        return $allow   # fail open: never block on an engine error
    }
}

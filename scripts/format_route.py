#!/usr/bin/env python3
"""
Routing Transparency Formatter

Takes the raw JSON output from `orch route` and formats it into the
architect summary Matt wants: job class detected, ranked candidates,
quota impact, why the winner was chosen. No jargon dump — decision-ready.

Usage:
    orch route --job-class X --text "..." | python format_route.py
    python format_route.py --input route_output.json
"""

import json
import sys
from pathlib import Path

CONTRACT_LABELS = {
    "local_resource": "Local (free)",
    "subscription_quota": "Subscription (metered)",
    "subscription_unlimited": "Subscription (flat)",
    "metered_extra_cost": "⚠️ METERED (costs cash)",
}

def format_route(data: dict) -> str:
    """Format a route decision as an architect summary."""
    lines = []
    
    state = data.get("state", "?")
    job_class = data.get("job_class", "?")
    classification = data.get("classification", {})
    decision = data.get("decision")
    
    # Header
    lines.append(f"ROUTING DECISION — {state}")
    lines.append("=" * 50)
    
    # Classification
    conf = classification.get("confidence", 0)
    reasoning = classification.get("reasoning", "?")
    matched = classification.get("matched_keywords", [])
    lines.append(f"Job Class: {job_class} (confidence: {conf:.0%})")
    lines.append(f"Reasoning: {reasoning}")
    if matched:
        lines.append(f"Matched: {', '.join(matched)}")
    lines.append("")
    
    if not decision:
        lines.append("No decision produced.")
        if data.get("error"):
            lines.append(f"Error: {data['error']}")
        return "\n".join(lines)
    
    # Chosen adapter
    adapter = decision.get("chosen_adapter", "?")
    lines.append(f"Chosen Adapter: {adapter}")
    
    # Candidates
    candidates = decision.get("candidates_considered", [])
    if candidates:
        lines.append("")
        lines.append(f"CANDIDATES ({len(candidates)} ranked):")
        lines.append("-" * 50)
        for i, c in enumerate(candidates[:10]):  # Top 10
            rank = i + 1
            worker = c.get("worker_id", "?")
            model = c.get("model_id", "?")
            surface = c.get("surface", "?")
            score = c.get("composite_score", 0)
            health = c.get("health_state", "?")
            contract = c.get("contract_type", "?")
            contract_label = CONTRACT_LABELS.get(contract, contract)
            
            # Top candidate gets arrow
            marker = "→" if rank == 1 else " "
            lines.append(f"{marker} {rank:2d}. {worker}")
            lines.append(f"      model: {model} | surface: {surface}")
            lines.append(f"      score: {score:.3f} | health: {health} | contract: {contract_label}")
            
            # Key scores for top 3
            if rank <= 3:
                quality = c.get("measured_quality_score", 0)
                fit = c.get("job_class_fit", 0)
                budget = c.get("budget_score", 0)
                speed = c.get("speed_score", 0)
                efficiency = c.get("token_efficiency_score", 0)
                lines.append(f"      quality={quality:.2f} fit={fit:.2f} budget={budget:.2f} speed={speed:.2f} efficiency={efficiency:.2f}")
            
            # Quota impact for subscription workers
            if contract in ("subscription_quota", "subscription_unlimited"):
                budget_score = c.get("budget_score", 0)
                if budget_score < 0.5:
                    lines.append(f"      ⚠ LOW QUOTA — budget score {budget_score:.2f}")
            lines.append("")
    
    # Why the winner — use first candidate since chosen_worker is often absent
    candidates = decision.get("candidates_considered", [])
    chosen = decision.get("chosen_worker") or (candidates[0] if candidates else {})
    override_note = ""
    
    if chosen:
        lines.append("WHY THIS WORKER WON:")
        lines.append("-" * 50)
        worker = chosen.get("worker_id", "?")
        contract = chosen.get("contract_type", "?")
        score = chosen.get("composite_score", 0)
        health = chosen.get("health_state", "?")
        
        reasons = []
        if health == "healthy":
            reasons.append("healthy (top of failover ladder)")
        if chosen.get("measured_quality_score", 0) > 0.8:
            reasons.append(f"high measured quality ({chosen['measured_quality_score']:.2f})")
        if chosen.get("job_class_fit", 0) >= 0.9:
            reasons.append(f"perfect job-class fit ({chosen['job_class_fit']:.2f})")
        if chosen.get("budget_score", 0) > 0.8:
            reasons.append(f"strong quota remaining ({chosen['budget_score']:.2f})")
        if contract == "local_resource":
            reasons.append("local resource (no quota cost)")
        if contract == "subscription_unlimited":
            reasons.append("flat subscription (no per-request cost)")
        
        lines.append(f"Worker: {worker}")
        lines.append(f"Score: {score:.3f}")
        for r in reasons:
            lines.append(f"  • {r}")
    
    # Surface-capability override (proven 2026-08-03 GovCon proof)
    # The brain routes to a model+surface combination; the translation engine
    # may override to a different dispatch surface with capabilities the brain's
    # pick lacks (e.g., web_search, web_extract, terminal). Show this transparently.
    chosen_surface = decision.get("chosen_adapter", "")
    if chosen_surface in ("ollama-http", "ollama-local") and candidates:
        # Brain picked a local model — check if it has web/search tools
        has_web = False
        for c in candidates[:3]:
            tools = c.get("tools_json", "[]")
            if "web" in tools.lower() or "search" in tools.lower():
                has_web = True
                break
        if not has_web:
            override_note = (
                "\n\nSURFACE OVERRIDE (translation engine):\n"
                + "-" * 50 + "\n"
                "Brain recommended local ollama model — no web/search tools.\n"
                "Translation engine overrides to: delegate_task (has web_search + web_extract)\n"
                "Reason: task requires web research capabilities the brain's pick lacks.\n"
                "This is the documented two-layer routing model: brain classifies,\n"
                "translation engine selects the dispatch surface with matching capabilities.\n"
            )
    
    # Quota impact summary
    if chosen:
        contract = chosen.get("contract_type", "?")
        lines.append("")
        lines.append("QUOTA IMPACT:")
        if contract == "local_resource":
            lines.append("  Zero — local compute, no subscription burn")
        elif contract == "subscription_unlimited":
            lines.append("  Minimal — flat subscription, no per-request metering")
        elif contract == "subscription_quota":
            lines.append(f"  Burns 1 quota unit — budget score {chosen.get('budget_score', 0):.2f} remaining")
        elif contract == "metered_extra_cost":
            lines.append("  ⚠ METERED — costs cash. Requires explicit approval.")
    
    if override_note:
        lines.append(override_note)
    
    return "\n".join(lines)


def main():
    if "--input" in sys.argv:
        idx = sys.argv.index("--input")
        path = sys.argv[idx + 1]
        data = json.loads(Path(path).read_text())
    else:
        data = json.load(sys.stdin)
    
    # Reconfigure stdout for UTF-8 on Windows (cp1252 can't encode arrows/symbols)
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print(format_route(data))


if __name__ == "__main__":
    main()
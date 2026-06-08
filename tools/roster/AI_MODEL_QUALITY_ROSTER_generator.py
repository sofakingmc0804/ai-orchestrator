#!/usr/bin/env python3
"""Build AI Model Quality Roster workbook (video-game stat-sheet style) + per-sheet CSVs.
Row grain = model x surface. Stats 0-10 (one decimal). Frontier benchmark-anchored (HIGH),
tail family-inherited (MEDIUM/LOW). Sources: AA Intelligence Index, SWE-bench Verified/Pro,
GPQA Diamond, ARC-AGI-2, HLE, Aider Polyglot, tau-bench/BFCL, MMMU-Pro, MTEB, image arena
(web-verified 2026-06-06/07) + AI_PROVIDER_MODEL_ROSTER_2026-06-06.md (on-Dell access)."""
import csv, json, os, subprocess
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.utils import get_column_letter

OUT = os.path.dirname(os.path.abspath(__file__))
CSVDIR = os.path.join(OUT, "AI_MODEL_QUALITY_ROSTER_csv")
os.makedirs(CSVDIR, exist_ok=True)
XLSX = os.path.join(OUT, "AI_MODEL_QUALITY_ROSTER_2026-06-07.xlsx")

SURF = {
 "anthropic_api": ("Anthropic API", "HTTP API", "Working (Claude plan)", "subscription_included", "No"),
 "claude_cli":    ("Claude Code CLI", "CLI", "Working", "subscription_included", "No"),
 "claude_desk":   ("Claude Desktop/Cowork", "Desktop app", "Working (this session)", "subscription_included", "No"),
 "bedrock":       ("Amazon Bedrock (Kiro)", "Aggregator", "Installed; login unverified", "metered (not configured)", "No"),
 "antigravity":   ("Google Antigravity", "IDE app", "Installed", "subscription_included", "No"),
 "openai_api":    ("OpenAI API", "HTTP API", "No standalone key", "metered (not configured)", "No"),
 "codex_cli":     ("Codex CLI", "CLI", "Working (ChatGPT plan)", "subscription_included", "No"),
 "copilot":       ("GitHub Copilot CLI", "CLI", "Working (premium req)", "subscription_included", "No"),
 "github_models": ("GitHub Models (AI Toolkit)", "Aggregator", "Configured", "subscription_included", "No"),
 "azure":         ("Azure AI Foundry", "Aggregator", "Not credentialed", "metered (not configured)", "No"),
 "gemini_api":    ("Google Gemini API", "HTTP API", "Working (re-authed)", "subscription_included", "No"),
 "gemini_cli":    ("Gemini CLI", "CLI", "Working (re-authed)", "subscription_included", "No"),
 "xai_api":       ("xAI API", "HTTP API", "Not-keyed", "metered (not configured)", "No"),
 "ollama_local":  ("Ollama (local)", "Local", "Running (free)", "local_free", "Yes"),
 "ollama_cloud":  ("Ollama Cloud", "Aggregator", "Keyed + usage-tracked", "metered (tracked)", "No"),
 "groq":          ("Groq", "Aggregator", "Not-keyed", "metered (not configured)", "No"),
 "together":      ("Together AI", "Aggregator", "Not-keyed", "metered (not configured)", "No"),
 "fireworks":     ("Fireworks AI", "Aggregator", "Not-keyed", "metered (not configured)", "No"),
 "perplexity":    ("Perplexity API", "Aggregator", "Not-keyed", "metered (not configured)", "No"),
 "openrouter":    ("OpenRouter", "Aggregator", "Not-keyed", "metered (not configured)", "No"),
 "mistral_api":   ("Mistral API", "HTTP API", "Not-keyed", "metered (not configured)", "No"),
 "deepseek_api":  ("DeepSeek API", "HTTP API", "Not-keyed", "metered (not configured)", "No"),
 "cohere_api":    ("Cohere API", "HTTP API", "Not-keyed", "metered (not configured)", "No"),
 "qwen_api":      ("Alibaba DashScope", "HTTP API", "Not-keyed", "metered (not configured)", "No"),
 "preview":       ("(provider preview - not GA)", "Preview", "Provider-documented; not GA", "unavailable", "No"),
}

def ollama_local_model_count():
    try:
        cp = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=15)
    except Exception:
        return None
    if cp.returncode != 0:
        return None
    rows = [ln for ln in cp.stdout.splitlines()[1:] if ln.strip()]
    return sum(1 for ln in rows if ":cloud" not in ln and " - " not in f" {ln} ")

LLM_GROUPS = [
 ("Research & Reasoning", ["Reasoning","Math","Research/Synthesis","Knowledge/Recency"]),
 ("Coding & Engineering", ["Code Gen","Agentic SWE","Polyglot/Refactor","Debug/Comprehension"]),
 ("Agentic & Tool Calling", ["Tool Calling","Autonomy/Long-horizon","Computer/Browser Use","Instruction Adherence"]),
 ("Creative & Language", ["Creative Writing","Prose/Editing","Summarization","Multilingual"]),
 ("Predictive & Analytical", ["Data Analysis","Forecasting","Structured Extraction","Quant Reasoning"]),
 ("Multimodal", ["Vision","Doc/Chart/OCR","Audio"]),
 ("Performance & Economy", ["Speed (tok/s)","Latency","Context Capacity","Cost-Efficiency"]),
 ("Reliability & Safety", ["Factuality","Consistency","Safety/Refusal","Robustness"]),
]
PWR_WEIGHTS = {"Research & Reasoning":0.19,"Coding & Engineering":0.17,"Agentic & Tool Calling":0.17,
 "Creative & Language":0.10,"Predictive & Analytical":0.09,"Performance & Economy":0.12,"Reliability & Safety":0.16}

def L(mid,name,prov,fam,ver,status,arch,rea,cod,ag,cr,pr,mm,pf,rl,ctx,mx,pin,pout,
      cons,ri,rq,roles,avoid,conf,src,surf,lv="2026-06-07"):
    return dict(mid=mid,name=name,prov=prov,fam=fam,ver=ver,status=status,arch=arch,
        rea=rea,cod=cod,ag=ag,cr=cr,pr=pr,mm=mm,pf=pf,rl=rl,ctx=ctx,mx=mx,pin=pin,pout=pout,
        cons=cons,ri=ri,rq=rq,roles=roles,avoid=avoid,conf=conf,src=src,surf=surf,lv=lv)

B="benchmark-anchored (AA Index, SWE-bench, GPQA, ARC-AGI-2, Aider, MMMU 2026-06)"
I="family-inherited estimate (scaled from flagship); verify before HIGH-stakes routing"

LLMS=[
 L("claude-opus-4-8","Claude Opus 4.8","Anthropic","Claude 4.x Opus","4.8","Current","Flagship Reasoner",
   (9.3,9.0,9.4,8.8),(9.2,8.9,9.0,9.1),(9.3,9.4,8.6,9.1),(9.1,9.3,9.1,8.6),(8.6,7.6,8.9,8.8),(8.6,8.6,""),
   (5.6,5.6,10,4.6),(8.7,8.8,9.2,8.6),"1M",64000,5.00,25.00,"critical",5,5,
   "Hardest reasoning, cross-file refactor, long-form analysis, RFP/proposal authoring","High-volume cheap classification; ultra-low-latency chat",
   "H","AA Index 61 (#1); SWE-bench Verified 88.6%; "+B,["anthropic_api","claude_cli","claude_desk","bedrock"]),
 L("claude-opus-4-7","Claude Opus 4.7","Anthropic","Claude 4.x Opus","4.7","Legacy (prior flagship)","Flagship Reasoner",
   (9.1,8.8,9.2,8.7),(9.0,8.8,8.9,9.0),(9.1,9.2,8.5,9.0),(9.0,9.2,9.0,8.5),(8.5,7.5,8.8,8.7),(8.5,8.5,""),
   (5.6,5.6,10,4.6),(8.6,8.7,9.1,8.5),"1M",64000,5.00,25.00,"critical",5,5,
   "Same as 4.8 where 4.8 unavailable; SWE-bench Pro leader 64.3%","",
   "H","AA Index 57; SWE-bench Verified 87.6%, Pro 64.3%; "+B,["anthropic_api","claude_cli","bedrock"]),
 L("claude-opus-4-6","Claude Opus 4.6","Anthropic","Claude 4.x Opus","4.6","Legacy","Flagship Reasoner",
   (9.0,8.6,9.0,8.6),(8.7,8.4,8.2,8.7),(8.9,9.0,8.4,8.9),(8.9,9.1,8.9,8.4),(8.3,7.3,8.6,8.5),(8.4,8.4,""),
   (5.8,5.8,10,4.6),(8.5,8.6,9.1,8.4),"1M",128000,5.00,25.00,"critical",5,4,
   "Adaptive-thinking reasoning, agentic IDE work (Antigravity default)","",
   "H","HLE 34.4%; Aider Polyglot 82.1%; "+B,["anthropic_api","claude_cli","antigravity","bedrock"]),
 L("claude-sonnet-4-6","Claude Sonnet 4.6","Anthropic","Claude 4.x Sonnet","4.6","Current","Workhorse",
   (8.6,8.4,8.7,8.4),(8.6,8.4,8.5,8.6),(9.0,8.9,8.4,8.8),(8.8,8.9,8.8,8.4),(8.2,7.3,8.6,8.3),(8.3,8.3,""),
   (7.2,7.2,10,7.0),(8.3,8.4,8.9,8.3),"1M",64000,3.00,15.00,"critical",5,4,
   "Best balance: daily agentic coding, tool workflows, drafting","Frontier-hardest math/abstract reasoning",
   "H","tau-bench retail 0.862/airline 0.700 (top multi-turn); "+B,["anthropic_api","claude_cli","antigravity","bedrock"]),
 L("claude-haiku-4-5-20251001","Claude Haiku 4.5","Anthropic","Claude 4.x Haiku","4.5","Current","Fast-Cheap",
   (7.4,7.0,7.0,7.2),(7.6,7.2,7.0,7.4),(7.8,7.4,6.8,7.8),(7.6,7.6,7.8,7.4),(7.0,6.2,7.6,7.0),(7.2,7.2,""),
   (8.8,8.8,8,7.5),(7.6,7.8,8.6,7.6),"200K",32000,1.00,5.00,"high",4,4,
   "Fast classification, routing, high-volume drafting, Copilot default","Deep multi-step reasoning; long-horizon autonomy",
   "H","AA-class fast tier; default Copilot model; "+B,["anthropic_api","claude_cli","copilot","bedrock"]),
 L("claude-mythos-preview","Claude Mythos Preview","Anthropic","Claude (specialized)","preview","Preview (invite-only)","Coder/Security Specialist",
   (9.2,8.8,9.0,8.6),(9.3,9.4,9.0,9.3),(9.2,9.2,8.6,9.1),(8.6,8.8,8.6,8.2),(8.4,7.6,8.8,8.6),(8.4,8.4,""),
   (5.5,5.5,10,4.0),(8.6,8.6,9.2,8.8),"1M",64000,None,None,"high",5,5,
   "Top SWE-bench coding; cybersecurity (Glasswing)","General access (invitation-only)",
   "M","SWE-bench Verified 93.9% (#1); MMMU-Pro 92.7%; access-gated so MEDIUM",["anthropic_api"]),
 L("gpt-5.5","GPT-5.5","OpenAI","GPT-5.x","5.5","Current","Flagship Reasoner",
   (9.4,9.2,9.2,9.0),(9.1,8.9,8.8,9.0),(9.2,9.2,8.8,9.2),(8.8,8.8,8.7,8.6),(8.6,7.8,8.8,9.0),(9.0,9.0,""),
   (6.0,6.0,10,4.5),(8.6,8.6,8.6,8.5),"1M",128000,5.00,30.00,"critical",5,5,
   "Frontier reasoning, agentic coding, tool orchestration","Cheapest bulk tasks",
   "H","AA Index 60; SWE-bench Verified 88.7%; GPQA 93.5%; ARC-AGI-2 85%; "+B,["openai_api","codex_cli","github_models","azure","copilot"]),
 L("gpt-5.5-pro","GPT-5.5 Pro","OpenAI","GPT-5.x","5.5-pro","Current","Flagship Reasoner (max compute)",
   (9.5,9.4,9.3,9.0),(9.2,9.0,8.9,9.1),(9.3,9.3,8.9,9.2),(8.8,8.8,8.7,8.6),(8.7,8.0,8.9,9.1),(9.1,9.1,""),
   (4.2,3.8,10,3.2),(8.7,8.7,8.7,8.7),"1M",128000,15.00,120.00,"critical",5,5,
   "Maximum-precision reasoning, hardest proofs/analysis","Latency- or cost-sensitive work",
   "H","Extended-compute GPT-5.5; "+B,["openai_api","codex_cli"]),
 L("gpt-5.4","GPT-5.4","OpenAI","GPT-5.x","5.4","Current","Flagship/Workhorse",
   (9.2,9.0,9.0,8.9),(9.0,8.7,8.6,8.9),(9.0,9.0,8.6,9.0),(8.7,8.7,8.6,8.5),(8.5,7.7,8.7,8.9),(9.2,9.0,""),
   (6.5,6.5,10,5.0),(8.5,8.5,8.6,8.4),"1M",128000,None,None,"critical",5,5,
   "Production agentic/coding workhorse, professional tasks","",
   "H","ARC-AGI-2 73.3%; SWE-bench Pro 59.1%; MMMU-Pro 94% (Pro); "+B,["openai_api","codex_cli","github_models","copilot"]),
 L("gpt-5.1","GPT-5.1","OpenAI","GPT-5.x","5.1","Current","Coder/Agentic",
   (8.8,8.6,8.6,8.6),(8.8,8.4,8.4,8.7),(8.8,8.8,8.4,8.8),(8.4,8.4,8.4,8.4),(8.2,7.4,8.4,8.6),(8.6,8.6,""),
   (6.8,6.8,10,5.5),(8.4,8.4,8.4,8.3),"1M",128000,None,None,"critical",5,4,
   "Strong agentic/coding with configurable reasoning","",
   "M","Prior GPT-5 gen; "+I,["openai_api","codex_cli","github_models"]),
 L("gpt-5","GPT-5","OpenAI","GPT-5.x","5","Legacy","Flagship (prior)",
   (8.6,8.4,8.4,8.6),(8.4,8.0,8.0,8.4),(8.4,8.4,8.0,8.6),(8.2,8.2,8.2,8.2),(8.0,7.2,8.2,8.4),(8.4,8.4,""),
   (6.8,6.8,10,5.5),(8.2,8.3,8.4,8.2),"1M",128000,None,None,"high",4,4,
   "Prior frontier; configurable reasoning effort","",
   "M","First GPT-5; "+I,["openai_api","github_models","azure"]),
 L("gpt-5-mini","GPT-5 Mini","OpenAI","GPT-5.x","5-mini","Current","Fast-Cheap",
   (7.8,7.4,7.2,7.6),(7.8,7.2,7.2,7.6),(7.8,7.4,7.0,7.8),(7.6,7.6,7.6,7.4),(7.2,6.4,7.6,7.2),(7.6,7.6,""),
   (8.6,8.6,9,8.0),(7.8,7.8,8.2,7.6),"400K",64000,None,None,"high",4,4,
   "Near-frontier at low latency/cost","Hardest reasoning",
   "M","Cost/latency-optimized GPT-5; "+I,["openai_api","codex_cli","github_models"]),
 L("gpt-5-nano","GPT-5 Nano","OpenAI","GPT-5.x","5-nano","Current","Fast-Cheap",
   (6.8,6.2,6.0,6.6),(6.8,6.0,6.0,6.6),(6.8,6.2,5.6,6.8),(6.8,6.8,6.8,6.6),(6.2,5.4,6.8,6.2),(6.6,6.6,""),
   (9.2,9.2,8,8.6),(7.0,7.2,8.0,7.0),"400K",64000,None,None,"medium",3,3,
   "Cheapest/fastest GPT-5 for simple classification","Anything needing depth",
   "M","Fastest GPT-5 tier; "+I,["openai_api","github_models"]),
 L("gpt-5.2-codex","GPT-5.2 Codex","OpenAI","GPT-5.x Codex","5.2-codex","Current","Coder Specialist",
   (8.6,8.2,8.2,8.4),(8.9,8.5,8.5,8.8),(8.8,8.8,8.6,8.8),(7.6,7.8,7.6,7.4),(7.8,7.0,8.2,8.2),("","",""),
   (6.6,6.6,9,5.6),(8.3,8.5,8.7,8.5),"400K",128000,None,None,"high",5,4,
   "Long-horizon agentic coding","Non-coding tasks",
   "M","Prior Codex generation; "+I,["openai_api","codex_cli"]),
 L("o3","o3","OpenAI","o-series","o3","Legacy (succeeded by GPT-5)","Reasoning Specialist",
   (8.8,8.8,8.4,8.2),(8.2,7.6,7.6,8.0),(8.0,8.0,7.6,8.2),(7.6,7.8,7.6,7.4),(8.0,7.4,8.0,8.4),(8.2,8.0,""),
   (5.5,5.0,7,5.0),(8.0,8.2,8.4,8.0),"200K",100000,None,None,"high",5,4,
   "Proof-heavy reasoning where GPT-5 unavailable","Fast/cheap tasks",
   "M","Pre-GPT-5 reasoning tier; "+I,["openai_api"]),
 L("o3-pro","o3 Pro","OpenAI","o-series","o3-pro","Legacy","Reasoning (max compute)",
   (9.0,9.0,8.6,8.2),(8.2,7.6,7.6,8.0),(8.0,8.0,7.6,8.2),(7.6,7.8,7.6,7.4),(8.2,7.6,8.0,8.6),(8.2,8.0,""),
   (4.0,3.6,7,4.0),(8.2,8.4,8.6,8.2),"200K",100000,None,None,"high",5,4,
   "Highest o-series reasoning precision","Latency-sensitive",
   "M","o3 + more compute; "+I,["openai_api"]),
 L("o4-mini","o4-mini","OpenAI","o-series","o4-mini","Legacy","Reasoning (fast)",
   (8.2,8.4,7.6,7.6),(7.8,7.2,7.0,7.6),(7.6,7.4,7.0,7.8),(7.0,7.2,7.0,7.0),(7.4,6.8,7.6,8.0),(7.6,7.4,""),
   (7.8,7.6,7,7.5),(7.6,7.8,8.0,7.6),"200K",100000,None,None,"high",4,4,
   "Fast cost-efficient reasoning","",
   "M","Fast o-series reasoning; "+I,["openai_api","codex_cli"]),
 L("gpt-4.1","GPT-4.1","OpenAI","GPT-4.x","4.1","Current (API)","Instruction/Tool Specialist",
   (8.0,7.5,7.8,8.2),(8.2,7.6,7.8,8.2),(8.8,8.2,7.6,9.0),(8.2,8.2,8.2,8.0),(7.8,7.0,8.4,7.8),(8.0,8.2,""),
   (7.5,7.5,10,7.0),(8.2,8.4,8.4,8.2),"1M",32000,None,None,"high",5,4,
   "Best non-reasoning instruction-following + tool calling, 1M ctx","Frontier reasoning/math",
   "H","Strong IFEval/tool-calling; 1M context; "+B,["openai_api","github_models","azure","copilot"]),
 L("gpt-4o","GPT-4o","OpenAI","GPT-4.x","4o","Current (API)","Multimodal Workhorse",
   (7.5,7.0,7.2,7.8),(7.6,6.8,7.0,7.6),(8.0,7.4,7.0,8.2),(8.0,8.0,8.0,8.0),(7.2,6.6,7.8,7.4),(8.4,8.2,8.6),
   (7.8,7.8,7,7.2),(7.8,8.0,8.2,7.8),"128K",16384,None,None,"high",4,4,
   "Live multimodal chat (vision+audio), realtime","Hardest reasoning/coding",
   "H","Mature multimodal baseline; "+B,["openai_api","copilot","azure"]),
 L("gpt-4o-mini","GPT-4o Mini","OpenAI","GPT-4.x","4o-mini","Current (API)","Fast-Cheap",
   (6.6,6.0,6.0,6.6),(6.6,5.8,5.8,6.4),(7.0,6.4,6.0,7.2),(7.0,7.0,7.0,6.8),(6.2,5.6,6.8,6.2),(7.2,7.0,7.0),
   (8.8,8.8,7,8.6),(7.0,7.2,7.6,7.0),"128K",16384,None,None,"medium",3,3,
   "Cheap multimodal classification/extraction","Depth tasks",
   "M","Small multimodal; "+I,["openai_api","azure"]),
 L("gpt-oss-120b","GPT-OSS-120B","OpenAI","GPT-OSS (open weight)","120b","Current","Open-Weight Flagship",
   (7.8,7.4,7.2,7.4),(7.4,6.8,7.0,7.4),(7.6,7.2,6.8,7.6),(7.2,7.2,7.2,7.0),(7.0,6.4,7.4,7.2),("","",""),
   (8.0,8.0,7,8.5),(7.2,7.4,7.4,7.2),"128K",32000,None,None,"high",4,4,
   "Best open-weight on H100; self-host","Local on this Dell (needs H100)",
   "M","Open-weight; via Groq/Together/Antigravity; "+I,["groq","together","antigravity","github_models"]),
 L("gpt-oss-20b","GPT-OSS-20B","OpenAI","GPT-OSS (open weight)","20b","Current","Open-Weight / Local",
   (6.8,6.2,6.0,6.4),(6.4,5.8,6.0,6.4),(6.6,6.2,5.6,6.6),(6.4,6.4,6.4,6.2),(6.0,5.4,6.4,6.2),("","",""),
   (7.0,6.5,7,9.5),(6.6,6.8,7.0,6.6),"128K",32000,None,None,"medium",3,3,
   "Low-latency open-weight; runs locally (slow) on Dell","Hard reasoning",
   "M","Local Ollama gpt-oss:20b verified on Dell; "+I,["ollama_local","groq"]),
 L("gemini-3.1-pro","Gemini 3.1 Pro","Google","Gemini 3.x","3.1-pro","Current","Flagship Reasoner",
   (9.4,9.2,9.3,9.0),(8.6,8.1,8.4,8.6),(8.8,8.8,8.6,8.8),(9.0,9.0,9.0,9.0),(8.6,7.8,8.6,8.8),(8.8,9.0,8.0),
   (6.5,6.5,10,7.5),(8.5,8.6,8.6,8.5),"1M",64000,2.00,12.00,"critical",5,5,
   "Top reasoning (GPQA #1), long-context multimodal, research","",
   "H","GPQA 94.1% (#1); ARC-AGI-2 77.1%; HLE 37.5%; MMMU-Pro 83.9%; "+B,["gemini_api","gemini_cli","antigravity","bedrock"]),
 L("gemini-3.5-flash","Gemini 3.5 Flash","Google","Gemini 3.x","3.5-flash","Current (latest GA)","Fast Frontier",
   (8.6,8.4,8.6,8.4),(8.4,8.0,8.2,8.4),(8.6,8.4,8.2,8.6),(8.6,8.6,8.6,8.6),(8.2,7.4,8.4,8.4),(8.6,8.6,7.8),
   (8.8,8.8,10,8.8),(8.2,8.3,8.4,8.2),"1M",64000,1.50,9.00,"high",5,4,
   "Frontier-class agentic/coding at low cost+latency, 1M ctx","",
   "H","Latest GA May 2026; "+B,["gemini_api","gemini_cli","antigravity"]),
 L("gemini-3.1-flash","Gemini 3.1 Flash","Google","Gemini 3.x","3.1-flash","Current","Fast multimodal",
   (8.2,8.0,8.2,8.2),(8.0,7.6,7.8,8.0),(8.2,8.0,7.8,8.2),(8.4,8.4,8.4,8.4),(7.8,7.0,8.0,8.0),(8.4,8.4,7.6),
   (9.0,9.0,10,9.0),(8.0,8.1,8.2,8.0),"1M",64000,None,None,"high",4,4,
   "Fast multimodal, high-volume agentic","",
   "M","Fast 3.1 tier; "+I,["gemini_api","gemini_cli","antigravity"]),
 L("gemini-3.1-flash-lite","Gemini 3.1 Flash Lite","Google","Gemini 3.x","3.1-flash-lite","Current","Budget",
   (7.4,7.0,7.2,7.4),(7.0,6.6,6.8,7.0),(7.2,7.0,6.8,7.4),(7.6,7.6,7.6,7.6),(6.8,6.2,7.2,7.0),(7.6,7.6,6.8),
   (9.4,9.4,10,9.6),(7.4,7.6,7.8,7.4),"1M",64000,None,None,"medium",3,3,
   "Ultra-budget high-volume multimodal","Depth",
   "M","Budget tier; "+I,["gemini_api","gemini_cli"]),
 L("gemini-2.5-pro","Gemini 2.5 Pro","Google","Gemini 2.5","2.5-pro","Legacy (value tier)","Workhorse",
   (8.4,8.2,8.4,8.6),(8.0,7.6,7.8,8.0),(8.2,8.0,7.8,8.2),(8.8,8.6,8.6,8.8),(8.0,7.2,8.2,8.2),(8.4,8.4,7.6),
   (7.5,7.5,10,8.0),(8.2,8.3,8.4,8.2),"1M",64000,None,None,"high",4,4,
   "Mature value tier; long LMArena leader","",
   "H","Long-time LMArena leader; "+B,["gemini_api","gemini_cli"]),
 L("gemini-2.5-flash","Gemini 2.5 Flash","Google","Gemini 2.5","2.5-flash","Legacy","Fast-Cheap",
   (7.8,7.4,7.4,7.8),(7.4,7.0,7.0,7.4),(7.6,7.2,7.0,7.6),(8.0,8.0,8.0,8.0),(7.2,6.6,7.6,7.4),(8.0,8.0,7.2),
   (9.0,9.0,10,9.2),(7.8,8.0,8.2,7.8),"1M",64000,None,None,"high",4,4,
   "Best $/perf in 2.5 gen, high-volume","",
   "M","Value workhorse; "+I,["gemini_api","gemini_cli"]),
 L("gemini-2.5-flash-lite","Gemini 2.5 Flash Lite","Google","Gemini 2.5","2.5-flash-lite","Legacy","Budget",
   (7.0,6.6,6.6,7.0),(6.6,6.2,6.4,6.6),(6.8,6.6,6.4,7.0),(7.2,7.2,7.2,7.2),(6.4,5.8,6.8,6.6),(7.4,7.4,6.6),
   (9.4,9.4,10,9.6),(7.2,7.4,7.6,7.2),"1M",64000,None,None,"medium",3,3,
   "Ultra-budget high-volume","Depth",
   "M","Value tier; "+I,["gemini_api","gemini_cli"]),
 L("grok-4.3","Grok 4.3","xAI","Grok 4.x","4.3","Current","Flagship Reasoner",
   (9.1,8.8,8.8,8.6),(8.4,8.0,8.0,8.4),(8.4,8.4,8.2,8.4),(8.4,8.4,8.2,8.2),(8.2,7.6,8.2,8.4),(8.2,8.0,7.5),
   (6.5,6.5,10,4.0),(8.0,8.0,7.8,7.8),"1M",None,None,None,"high",5,4,
   "Reasoning-first flagship, native video input","Cost-sensitive ($300/mo tier)",
   "M","Released 2026-04-17 ($300/mo); less independent benchmarking; MEDIUM",["xai_api","openrouter"]),
 L("grok-build-0.1","Grok Build 0.1","xAI","Grok (specialized)","build-0.1","Current (beta)","Coder Specialist",
   (8.4,8.0,8.0,8.2),(8.6,8.4,8.2,8.6),(8.6,8.4,8.2,8.4),(7.6,7.8,7.6,7.4),(7.8,7.0,8.2,8.2),("","",""),
   (7.0,7.0,8,5.0),(7.8,8.0,7.8,7.8),"256K",None,None,None,"high",5,4,
   "Dedicated software-engineering model","Non-coding",
   "M","API beta 2026-05-28; "+I,["xai_api"]),
 L("grok-4.20","Grok 4.20","xAI","Grok 4.x","4.20","Current","Reasoner (low-halluc)",
   (8.8,8.6,8.4,8.6),(8.2,7.8,7.8,8.2),(8.2,8.2,8.0,8.2),(8.2,8.2,8.0,8.0),(8.0,7.4,8.0,8.2),(8.0,7.8,7.0),
   (6.8,6.8,10,4.5),(8.4,8.2,7.8,8.2),"1M",None,None,None,"high",4,4,
   "High-performance, low hallucination","",
   "L","xAI variant; "+I,["xai_api","openrouter"]),
 L("meta-llama/Llama-4-Maverick-17B-128E","Llama 4 Maverick","Meta","Llama 4","maverick","Current","Open-Weight Workhorse",
   (8.0,7.6,7.6,8.0),(7.8,7.2,7.4,7.8),(7.8,7.4,7.0,7.8),(8.0,8.0,7.8,8.2),(7.4,6.6,7.6,7.4),(8.0,7.8,""),
   (8.5,8.5,9,9.0),(7.4,7.6,7.6,7.4),"1M",None,None,None,"high",4,4,
   "Cheap open-weight multimodal, high throughput via Groq","Frontier reasoning",
   "M","~400B/17B-active MoE; "+I,["groq","together","fireworks","perplexity","bedrock","github_models","openrouter"]),
 L("meta-llama/Llama-4-Scout-17B-16E","Llama 4 Scout","Meta","Llama 4","scout","Current","Open-Weight (long ctx)",
   (7.4,7.0,7.0,7.4),(7.2,6.6,6.8,7.2),(7.2,7.0,6.6,7.4),(7.6,7.6,7.4,7.8),(6.8,6.2,7.2,6.8),(7.6,7.4,""),
   (9.0,9.0,10,9.2),(7.0,7.2,7.4,7.0),"10M",None,None,None,"medium",3,3,
   "10M-context retrieval over huge inputs, ultra-fast on Groq","Hardest reasoning",
   "M","109B/17B-active, 10M ctx; "+I,["groq","together","fireworks","perplexity","bedrock","github_models","openrouter"]),
 L("meta-llama/Llama-3.1-405B-Instruct","Llama 3.1 405B","Meta","Llama 3.1","405b","Legacy","Open-Weight (large)",
   (7.6,7.0,7.0,7.8),(7.4,6.6,6.8,7.4),(7.2,7.0,6.4,7.4),(7.8,7.8,7.6,7.8),(7.0,6.4,7.4,7.2),("","",""),
   (6.0,6.0,4,7.0),(7.4,7.6,7.6,7.4),"128K",None,None,None,"high",4,4,
   "Largest open-weight 3.1; self-host quality","Latency-sensitive",
   "M","Best-quality Llama 3.1; "+I,["together","bedrock","openrouter"]),
 L("meta-llama/Llama-3.3-70B-Instruct","Llama 3.3 70B","Meta","Llama 3.3","70b","Legacy","Open-Weight (mid)",
   (7.2,6.6,6.6,7.2),(7.0,6.2,6.6,7.0),(7.0,6.6,6.2,7.2),(7.4,7.4,7.2,7.6),(6.6,6.0,7.0,6.8),("","",""),
   (8.0,8.0,7,8.5),(7.0,7.2,7.4,7.0),"128K",None,None,None,"high",4,3,
   "Cheap mid-tier open-weight, fast on Groq","",
   "M","3.3 generation; "+I,["groq","together","bedrock","github_models","openrouter"]),
 L("meta-llama/Llama-3.1-70B-Instruct","Llama 3.1 70B","Meta","Llama 3.1","70b","Legacy","Open-Weight (mid)",
   (7.0,6.4,6.4,7.0),(6.8,6.0,6.4,6.8),(6.8,6.4,6.0,7.0),(7.2,7.2,7.0,7.4),(6.4,5.8,6.8,6.6),("","",""),
   (8.2,8.2,7,8.6),(6.8,7.0,7.2,6.8),"128K",None,None,None,"high",4,3,
   "Cheap mid open-weight","",
   "M","3.1 mid; "+I,["groq","together","bedrock","openrouter"]),
 L("meta-llama/Llama-3.1-8B-Instruct","Llama 3.1 8B","Meta","Llama 3.1","8b","Legacy","Open-Weight / Fast",
   (6.0,5.4,5.4,6.0),(5.8,5.0,5.4,5.8),(6.0,5.6,5.0,6.2),(6.4,6.4,6.2,6.6),(5.6,5.0,6.2,5.8),("","",""),
   (9.2,9.2,7,9.4),(6.2,6.4,6.6,6.2),"128K",None,None,None,"medium",3,3,
   "Ultra-fast cheap open-weight (Groq instant)","Depth",
   "M","3.1 small; "+I,["groq","together","bedrock","openrouter"]),
 L("meta-llama/Llama-3.2-90B-Vision-Instruct","Llama 3.2 90B Vision","Meta","Llama 3.2","90b-vision","Legacy","Open Multimodal",
   (7.0,6.4,6.4,7.0),(6.6,5.8,6.2,6.6),(6.6,6.2,5.8,6.8),(7.0,7.0,6.8,7.2),(6.4,5.8,6.6,6.4),(7.6,7.4,""),
   (7.5,7.5,7,8.5),(6.8,7.0,7.0,6.8),"128K",None,None,None,"high",3,3,
   "Open-weight vision-language","",
   "M","Llama vision; "+I,["together","bedrock","openrouter"]),
 L("mistral-large-3-25-12","Mistral Large 3","Mistral","Mistral Large","3","Current","Open/Flagship (MoE)",
   (8.2,7.8,7.8,8.0),(8.0,7.4,7.6,8.0),(8.0,7.6,7.4,8.0),(8.2,8.2,8.0,8.6),(7.6,7.0,8.0,7.8),("","",""),
   (7.5,7.5,7,7.5),(7.8,7.8,7.8,7.6),"256K",None,None,None,"high",4,4,
   "European flagship, strong multilingual, tool use","",
   "M","Top-tier Mistral MoE; "+I,["mistral_api","bedrock","openrouter"]),
 L("devstral-2-25-12","Devstral 2","Mistral","Devstral","2","Current","Coder Specialist",
   (7.6,7.2,7.2,7.6),(8.4,8.0,8.0,8.4),(8.2,8.0,7.6,8.2),(7.2,7.4,7.2,7.4),(7.4,6.8,7.8,7.8),("","",""),
   (7.8,7.8,7,8.0),(7.6,7.8,7.6,7.6),"256K",None,None,None,"high",4,4,
   "Open coding/agentic SWE model","Non-coding creative",
   "M","Mistral software-dev model; "+I,["mistral_api","openrouter"]),
 L("mistral-medium-3-5-26-04","Mistral Medium 3.5","Mistral","Mistral Medium","3.5","Current","Workhorse",
   (7.8,7.4,7.4,7.6),(7.6,7.0,7.2,7.6),(7.6,7.2,7.0,7.6),(7.8,7.8,7.6,8.2),(7.2,6.6,7.6,7.4),("","",""),
   (8.0,8.0,7,8.2),(7.4,7.6,7.6,7.4),"128K",None,None,None,"high",4,4,
   "Balanced mid-tier, multilingual","",
   "M","Latest medium; "+I,["mistral_api","openrouter"]),
 L("mistral-small-4-0-26-03","Mistral Small 4","Mistral","Mistral Small","4","Current","Fast-Cheap",
   (7.0,6.6,6.6,7.0),(7.0,6.4,6.6,7.0),(7.0,6.6,6.2,7.0),(7.4,7.4,7.2,7.8),(6.6,6.0,7.0,6.8),("","",""),
   (8.6,8.6,6,9.0),(7.0,7.2,7.2,7.0),"128K",None,None,None,"high",3,3,
   "Latest small; cheap multilingual","",
   "M","Latest small; "+I,["mistral_api","openrouter"]),
 L("magistral-medium-1-2-25-09","Magistral Medium 1.2","Mistral","Magistral","1.2","Current","Reasoning",
   (8.0,8.0,7.4,7.6),(7.4,6.8,6.8,7.4),(7.2,7.2,6.6,7.4),(7.2,7.2,7.0,7.6),(7.4,6.8,7.4,8.0),("","",""),
   (7.0,7.0,6,7.5),(7.4,7.6,7.4,7.4),"128K",None,None,None,"high",4,3,
   "Mistral reasoning model","",
   "M","Reasoning variant; "+I,["mistral_api","openrouter"]),
 L("codestral-25-08","Codestral","Mistral","Codestral","25-08","Current","Coder Specialist",
   (6.6,6.2,6.2,6.6),(8.0,7.2,7.4,7.8),(7.2,6.8,6.0,7.2),(6.4,6.6,6.4,6.6),(6.6,6.0,7.0,7.0),("","",""),
   (8.6,8.6,7,8.5),(7.0,7.2,7.2,7.0),"256K",None,None,None,"high",4,4,
   "Fast code completion/generation","Non-coding",
   "M","Mistral code model; "+I,["mistral_api","openrouter"]),
 L("ministral-3-8b-25-12","Ministral 3 (8B)","Mistral","Ministral 3","8b","Current","Edge / Local",
   (6.4,5.8,5.6,6.2),(6.2,5.4,5.6,6.0),(6.2,5.8,5.0,6.2),(6.6,6.6,6.4,6.8),(5.8,5.2,6.2,6.0),("","",""),
   (8.5,8.0,5,9.5),(6.4,6.6,6.6,6.4),"128K",None,None,None,"medium",3,3,
   "Edge-optimized local chat (runs on Dell)","Depth tasks",
   "L","Edge 8B; local on Dell (ministral-3:8b); "+I,["ollama_local","mistral_api"]),
 L("deepseek-v4-pro","DeepSeek V4 Pro","DeepSeek","DeepSeek V4","v4-pro","Current","Open Flagship (cheap)",
   (9.0,8.8,8.6,8.4),(8.8,8.6,8.4,8.8),(8.4,8.4,8.0,8.4),(8.2,8.2,8.0,8.0),(8.2,7.4,8.4,8.4),("","",""),
   (7.0,7.0,10,9.5),(7.8,7.8,7.6,7.8),"1M",None,0.14,None,"high",5,4,
   "Frontier reasoning/coding at price floor ($0.14/M)","Native multimodal",
   "H","Leads SWE-bench/LiveCodeBench; 1.6T/49B-active; "+B,["deepseek_api","together","fireworks","openrouter"]),
 L("deepseek-v4-flash","DeepSeek V4 Flash","DeepSeek","DeepSeek V4","v4-flash","Current","Fast-Cheap",
   (8.0,7.8,7.4,7.6),(7.8,7.4,7.2,7.8),(7.6,7.4,7.0,7.6),(7.6,7.6,7.4,7.4),(7.2,6.6,7.6,7.4),("","",""),
   (8.6,8.6,10,9.6),(7.4,7.6,7.4,7.4),"1M",None,None,None,"medium",4,3,
   "Fast cheap long-context","",
   "M","284B/13B-active; "+I,["deepseek_api","together","openrouter"]),
 L("deepseek-v3-1","DeepSeek V3.1","DeepSeek","DeepSeek V3","v3.1","Legacy","General/long-ctx",
   (7.8,7.4,7.2,7.4),(7.6,7.0,7.0,7.6),(7.2,7.0,6.6,7.4),(7.4,7.4,7.2,7.2),(7.0,6.4,7.4,7.2),("","",""),
   (7.5,7.5,9,9.2),(7.2,7.4,7.2,7.2),"128K",None,None,None,"high",4,3,
   "General chat + long context, cheap","",
   "M","Prior DeepSeek; "+I,["deepseek_api","together","openrouter"]),
 L("deepseek-r1","DeepSeek R1","DeepSeek","DeepSeek R1","r1","Legacy","Reasoning (open)",
   (8.4,8.6,7.8,7.8),(7.8,7.4,7.2,7.8),(7.4,7.4,6.8,7.6),(7.4,7.4,7.2,7.2),(7.6,7.2,7.6,8.0),("","",""),
   (6.5,6.0,8,9.0),(7.2,7.4,7.0,7.2),"128K",None,None,None,"high",4,3,
   "Open multi-step reasoning; distills run locally","",
   "M","Open reasoning model; R1-distill local; "+I,["deepseek_api","ollama_local","groq","github_models","bedrock"]),
 L("phi-4","Phi-4","Microsoft","Phi-4","4","Current","Small Reasoner",
   (7.6,7.6,6.6,6.8),(7.0,6.2,6.4,7.0),(6.8,6.4,5.8,7.0),(6.8,6.8,6.8,6.6),(6.6,6.0,7.0,7.4),("","",""),
   (8.4,8.0,4,9.0),(7.0,7.2,7.0,7.0),"16K",None,None,None,"medium",3,3,
   "Strong reasoning/math for size; on-device","Long context; agentic autonomy",
   "M","Data-efficient SLM; "+I,["github_models","azure"]),
 L("phi-4-reasoning-plus","Phi-4 Reasoning+","Microsoft","Phi-4","4-reasoning-plus","Current","Small Reasoner",
   (8.0,8.2,6.8,6.8),(7.2,6.6,6.6,7.2),(6.8,6.6,5.8,7.0),(6.6,6.6,6.6,6.4),(7.0,6.4,7.0,7.6),("","",""),
   (8.0,7.6,4,8.8),(7.2,7.2,7.0,7.0),"32K",None,None,None,"high",4,3,
   "RL-enhanced compact reasoning/math","",
   "M","Reasoning-tuned Phi-4; "+I,["github_models","azure"]),
 L("phi-4-multimodal","Phi-4 Multimodal","Microsoft","Phi-4","4-multimodal","Current","Small Multimodal",
   (7.0,6.6,6.0,6.4),(6.4,5.6,5.8,6.4),(6.2,5.8,5.2,6.4),(6.6,6.6,6.6,7.2),(6.2,5.6,6.6,6.6),(7.4,7.2,7.0),
   (8.2,8.0,4,9.0),(6.8,7.0,7.0,6.8),"128K",None,None,None,"medium",3,3,
   "Compact text+vision+audio, 20+ langs","Hardest reasoning",
   "M","Phi multimodal; "+I,["github_models","azure"]),
 L("phi-4-mini","Phi-4 Mini","Microsoft","Phi-4","4-mini","Current","Small / Edge",
   (6.6,6.4,5.6,6.2),(6.2,5.4,5.6,6.0),(6.0,5.6,5.0,6.2),(6.2,6.2,6.2,6.6),(6.0,5.4,6.4,6.4),("","",""),
   (8.8,8.4,5,9.4),(6.6,6.8,6.8,6.6),"128K",None,None,None,"medium",3,3,
   "Efficient multilingual SLM","Depth",
   "M","Phi mini; "+I,["github_models","azure"]),
 L("command-a-plus-05-2026","Command A+","Cohere","Command A","a-plus","Current","Enterprise/RAG",
   (8.0,7.4,7.8,7.8),(7.6,7.0,7.2,7.6),(8.2,7.8,7.2,8.2),(8.0,8.0,8.0,8.4),(7.6,7.0,8.0,7.6),("","",""),
   (7.8,7.8,7,7.0),(8.0,8.0,8.2,7.8),"256K",None,None,None,"high",4,4,
   "Enterprise RAG, tool use, multilingual, citations","Frontier reasoning",
   "M","Cohere flagship (BFCL-strong line); "+I,["cohere_api","bedrock"]),
 L("command-a","Command A","Cohere","Command A","a","Current","Enterprise/RAG",
   (7.6,7.0,7.4,7.6),(7.2,6.6,6.8,7.2),(8.0,7.6,7.0,8.0),(7.8,7.8,7.8,8.2),(7.4,6.8,7.8,7.4),("","",""),
   (8.0,8.0,7,7.5),(7.8,7.8,8.0,7.6),"256K",None,None,None,"high",4,4,
   "Enterprise RAG/tool use","",
   "M","Cohere current; "+I,["cohere_api","bedrock"]),
 L("command-r-plus","Command R+","Cohere","Command R","r-plus","Legacy","Enterprise/RAG",
   (7.2,6.6,7.2,7.2),(6.8,6.2,6.4,6.8),(7.6,7.2,6.6,7.6),(7.4,7.4,7.4,7.8),(7.0,6.4,7.4,7.0),("","",""),
   (8.0,8.0,6,7.5),(7.6,7.6,7.8,7.4),"128K",None,2.50,10.00,"high",4,4,
   "RAG with citations, tool use","",
   "M","R+ ($2.50/$10); "+I,["cohere_api","bedrock"]),
 L("command-r","Command R","Cohere","Command R","r","Legacy","Enterprise (cheap)",
   (6.6,6.0,6.6,6.6),(6.2,5.6,5.8,6.2),(7.0,6.6,6.0,7.0),(6.8,6.8,6.8,7.2),(6.4,5.8,6.8,6.4),("","",""),
   (8.6,8.6,6,9.0),(7.2,7.2,7.4,7.0),"128K",None,0.15,0.60,"medium",3,3,
   "Cheap RAG/chat","",
   "M","R ($0.15/$0.60); "+I,["cohere_api","bedrock"]),
 L("qwen3.7-max","Qwen3.7 Max","Alibaba","Qwen3.7","3.7-max","Current","Open Flagship",
   (8.8,8.6,8.4,8.2),(8.4,8.0,8.2,8.4),(8.2,8.2,7.8,8.2),(8.2,8.2,8.0,8.6),(8.0,7.2,8.2,8.4),("","",""),
   (7.5,7.5,8,8.5),(7.8,7.8,7.6,7.8),"256K",None,None,None,"high",4,4,
   "Text-only flagship, strong multilingual/coding","",
   "M","Qwen3.7 May 2026; "+I,["qwen_api","ollama_cloud","together","fireworks","openrouter"]),
 L("qwen3.7-plus","Qwen3.7 Plus","Alibaba","Qwen3.7","3.7-plus","Current","Open Multimodal Flagship",
   (8.6,8.4,8.2,8.0),(8.2,7.8,8.0,8.2),(8.2,8.0,7.8,8.2),(8.2,8.2,8.0,8.6),(7.8,7.0,8.0,8.2),(8.4,8.4,7.6),
   (7.5,7.5,8,8.5),(7.8,7.8,7.6,7.8),"256K",None,None,None,"high",4,4,
   "Multimodal flagship, multilingual","",
   "M","Qwen3.7 multimodal; "+I,["qwen_api","together","openrouter"]),
 L("qwen3.5-122b-a10b","Qwen3.5 122B-A10B","Alibaba","Qwen3.5","3.5-122b","Current","Open MoE",
   (8.2,8.0,7.8,7.8),(8.0,7.6,7.8,8.0),(8.0,7.8,7.4,8.0),(7.8,7.8,7.6,8.2),(7.6,6.8,7.8,8.0),("","",""),
   (7.8,7.8,8,8.6),(7.4,7.6,7.4,7.4),"256K",None,None,None,"high",4,4,
   "Large open MoE","",
   "M","Qwen3.5 MoE; "+I,["qwen_api","together","fireworks","openrouter"]),
 L("qwen3-235b-a22b","Qwen3 235B-A22B","Alibaba","Qwen3","235b-a22b","Current","Open MoE Flagship",
   (8.4,8.2,8.0,7.8),(8.2,7.8,8.0,8.2),(8.4,8.0,7.6,8.4),(8.0,8.0,7.8,8.2),(7.8,7.0,8.0,8.2),("","",""),
   (7.5,7.5,8,8.5),(7.6,7.6,7.4,7.6),"256K",None,None,None,"high",4,4,
   "Strong open MoE; BFCL-leading tool calling","",
   "M","BFCL/agentic-strong open model; "+I,["qwen_api","together","fireworks","bedrock","openrouter"]),
 L("qwen3-32b","Qwen3 32B","Alibaba","Qwen3","32b","Current","Open Dense (tool-strong)",
   (7.6,7.2,7.0,7.2),(7.4,6.8,7.0,7.4),(8.0,7.6,6.8,7.8),(7.4,7.4,7.2,7.8),(7.2,6.6,7.4,7.4),("","",""),
   (8.0,8.0,7,9.0),(7.2,7.4,7.2,7.2),"128K",None,None,None,"high",4,4,
   "Strong tool-calling for size (BFCL 75.7%)","",
   "M","BFCL-strong dense; "+I,["qwen_api","together","openrouter"]),
 L("qwen2.5-coder:7b","Qwen2.5 Coder 7B (local)","Alibaba","Qwen2.5 Coder","7b","Current","Coder / Local",
   (6.2,5.6,5.4,6.0),(7.2,6.2,6.6,7.0),(6.4,6.0,5.0,6.4),(6.0,6.0,6.0,6.0),(6.0,5.4,6.6,6.2),("","",""),
   (8.0,7.5,5,9.6),(6.4,6.6,6.4,6.4),"128K",None,None,None,"medium",3,3,
   "Local code completion/generation on Dell","Large-repo agentic SWE",
   "L","Local Ollama qwen2.5-coder:7b; "+I,["ollama_local"]),
 L("glm-5.1","GLM-5.1","Zhipu AI","GLM-5","5.1","Current","Open Flagship (agentic)",
   (8.4,8.0,8.0,7.8),(8.4,8.0,8.0,8.4),(8.6,8.2,8.0,8.4),(8.0,8.0,7.8,8.0),(7.8,7.0,8.0,8.2),("","",""),
   (7.5,7.5,7,8.5),(7.6,7.6,7.4,7.6),"200K",None,None,None,"high",4,4,
   "Open agentic/coding leader; tool calling (BFCL-strong)","",
   "M","GLM-5 SWE-bench 77.8% (open lead); BFCL-strong; "+B,["ollama_cloud","openrouter"]),
 L("glm-5","GLM-5","Zhipu AI","GLM-5","5","Current","Open (general)",
   (8.0,7.6,7.6,7.6),(8.0,7.6,7.6,8.0),(8.2,7.8,7.6,8.0),(7.8,7.8,7.6,7.8),(7.6,6.8,7.8,8.0),("","",""),
   (7.5,7.5,7,8.6),(7.4,7.4,7.2,7.4),"200K",None,None,None,"high",4,4,
   "Open general/agentic (SWE 77.8%)","",
   "M","GLM-5 open; "+B,["ollama_cloud","openrouter"]),
 L("minimax-m2.7","MiniMax M2.7","MiniMax","MiniMax M2","2.7","Legacy (prior)","Open (agentic/coding)",
   (8.2,7.8,7.8,7.6),(8.2,7.8,7.8,8.2),(8.4,8.0,7.8,8.2),(7.8,7.8,7.6,7.8),(7.6,7.0,7.8,8.0),("","",""),
   (7.5,7.5,8,8.6),(7.4,7.4,7.2,7.4),"1M",None,None,None,"high",4,4,
   "Agentic/coding open MoE, long context","",
   "M","Large MoE; via Ollama Cloud (tracked); "+I,["ollama_cloud","together","openrouter"]),
 L("minimax-m2.5","MiniMax M2.5","MiniMax","MiniMax M2","2.5","Legacy","Open (agentic)",
   (8.0,7.6,7.6,7.4),(8.0,7.6,7.6,8.0),(8.2,7.8,7.6,8.0),(7.6,7.6,7.4,7.6),(7.4,6.8,7.6,7.8),("","",""),
   (7.5,7.5,8,8.6),(7.2,7.2,7.0,7.2),"1M",None,None,None,"high",4,3,
   "Prior MiniMax MoE","",
   "M","M2.5; "+I,["together","openrouter"]),
 L("kimi-k2.6","Kimi K2.6","Moonshot","Kimi K2","2.6","Current","Open Flagship (cheap)",
   (8.6,8.4,8.2,8.2),(8.8,8.6,8.4,8.6),(8.6,8.6,8.2,8.4),(8.2,8.2,8.0,8.2),(8.0,7.2,8.2,8.4),(8.0,8.0,""),
   (7.5,7.5,8,9.0),(7.8,7.8,7.6,7.8),"256K",None,0.60,None,"high",5,4,
   "Beat GPT-5.4 on SWE-bench Pro at $0.60/M; top open MMMU-Pro","",
   "H","SWE-bench Pro leader vs V4; MMMU-Pro 0.801 (open #1); "+B,["together","fireworks","groq","openrouter"]),
 L("kimi-k2.5","Kimi K2.5","Moonshot","Kimi K2","2.5","Legacy","Open (cheap)",
   (8.2,8.0,7.8,7.8),(8.4,8.2,8.0,8.2),(8.2,8.2,7.8,8.0),(8.0,8.0,7.8,8.0),(7.6,7.0,7.8,8.0),("","",""),
   (7.5,7.5,8,9.0),(7.6,7.6,7.4,7.6),"256K",None,None,None,"high",4,4,
   "Prior Kimi flagship, cheap","",
   "M","K2.5 via Together/Fireworks; "+I,["together","fireworks","openrouter"]),
 L("amazon.nova-pro-v1:0","Amazon Nova Pro","Amazon","Nova","pro","Current","Workhorse (Bedrock)",
   (7.4,6.8,6.8,7.2),(7.0,6.2,6.4,7.0),(7.4,7.0,6.6,7.6),(7.4,7.4,7.2,7.4),(6.8,6.2,7.4,7.0),(7.8,7.8,""),
   (8.0,8.0,8,8.5),(7.4,7.6,7.6,7.4),"300K",None,None,None,"high",4,4,
   "Balanced Bedrock-native multimodal, agentic","Frontier reasoning",
   "M","AWS Nova flagship; "+I,["bedrock"]),
 L("amazon.nova-lite-v1:0","Amazon Nova Lite","Amazon","Nova","lite","Current","Fast-Cheap (Bedrock)",
   (6.6,6.0,6.0,6.6),(6.4,5.8,5.8,6.4),(6.8,6.4,6.0,7.0),(6.8,6.8,6.8,6.8),(6.2,5.6,6.8,6.2),(7.2,7.2,""),
   (9.0,9.0,8,9.2),(7.0,7.2,7.4,7.0),"300K",None,None,None,"medium",3,3,
   "Low-cost multimodal at scale","Depth",
   "M","Nova low-cost tier; "+I,["bedrock"]),
 L("amazon.nova-micro-v1:0","Amazon Nova Micro","Amazon","Nova","micro","Current","Fast-Cheap (text)",
   (6.0,5.4,5.4,6.0),(5.8,5.0,5.2,5.8),(6.2,5.8,5.2,6.4),(6.2,6.2,6.2,6.2),(5.6,5.0,6.2,5.6),("","",""),
   (9.4,9.4,7,9.4),(6.8,7.0,7.2,6.8),"128K",None,None,None,"medium",3,3,
   "Lowest-cost text-only Bedrock tasks","Multimodal/depth",
   "M","Nova text micro; "+I,["bedrock"]),
 L("ai21.jamba-1-5-large-v1:0","Jamba 1.5 Large","AI21 Labs","Jamba","1.5-large","Current","Hybrid SSM (long ctx)",
   (7.0,6.4,6.6,6.8),(6.6,6.0,6.2,6.6),(6.8,6.4,6.0,7.0),(7.0,7.0,7.0,7.2),(6.6,6.0,7.0,6.8),("","",""),
   (8.0,8.0,9,7.5),(6.8,7.0,7.2,6.8),"256K",None,None,None,"high",3,3,
   "Hybrid Mamba-Transformer, very long context","",
   "L","Jamba via Bedrock; "+I,["bedrock"]),
 L("writer.palmyra-x5-v1:0","Palmyra X5","Writer","Palmyra","x5","Current","Enterprise Writing",
   (7.4,6.8,7.0,7.2),(6.8,6.2,6.4,6.8),(7.2,6.8,6.4,7.4),(8.0,8.2,8.0,7.6),(7.0,6.4,7.2,7.0),("","",""),
   (8.0,8.0,7,7.5),(7.4,7.4,7.6,7.4),"128K",None,None,None,"high",4,4,
   "Enterprise content/writing workflows","Hard reasoning/coding",
   "L","Palmyra via Bedrock; "+I,["bedrock"]),
 L("qwen3-coder-next:cloud","Qwen3 Coder Next (cloud)","Alibaba","Qwen Coder","next","Current","Coder (cloud-relay)",
   (7.6,7.2,7.2,7.4),(8.4,8.0,8.2,8.4),(8.2,8.0,7.4,8.0),(7.2,7.4,7.2,7.4),(7.4,6.8,7.8,7.8),("","",""),
   (7.8,7.8,9,8.8),(7.4,7.6,7.4,7.4),"256K",None,None,None,"high",4,4,
   "Agentic coding via Ollama Cloud (tracked)","Non-coding",
   "M","Routed through 11435 tracker; "+I,["ollama_cloud"]),
 L("qwen3.5:9b","Qwen3.5 9B (local)","Alibaba","Qwen3.5","9b","Current (local)","Local General",
   (6.6,6.2,5.8,6.2),(6.4,5.6,5.8,6.2),(6.2,5.8,5.0,6.4),(6.4,6.4,6.2,6.8),(6.0,5.4,6.4,6.2),("","",""),
   (8.0,7.5,5,9.8),(6.4,6.6,6.6,6.4),"128K",None,None,None,"medium",3,3,
   "Best local general model on Dell; offline/free","Large-context, hard reasoning",
   "L","Local Ollama qwen3.5:9b; GPQA-strong family (9B ~81.7 reported); "+I,["ollama_local"]),
 L("qwen3:8b","Qwen3 8B (local)","Alibaba","Qwen3","8b","Current (local)","Local General",
   (6.2,5.8,5.4,6.0),(6.0,5.2,5.4,6.0),(6.0,5.6,4.8,6.2),(6.2,6.2,6.0,6.6),(5.8,5.2,6.2,6.0),("","",""),
   (8.2,7.8,5,9.8),(6.2,6.4,6.4,6.2),"128K",None,None,None,"low",3,3,
   "Local general fallback; offline/free","",
   "L","Local Ollama qwen3:8b; "+I,["ollama_local"]),
 L("deepseek-r1:8b","DeepSeek R1 8B (local)","DeepSeek","DeepSeek R1 distill","8b","Current (local)","Local Reasoning",
   (6.8,7.0,5.6,5.8),(6.0,5.4,5.4,6.0),(5.8,5.6,4.6,6.0),(6.0,6.0,5.8,6.0),(6.0,5.6,6.0,6.4),("","",""),
   (7.5,7.0,5,9.8),(6.0,6.2,6.0,6.0),"128K",None,None,None,"low",3,3,
   "Local step-by-step reasoning; offline/free","Tool use, long horizon",
   "L","Local Ollama deepseek-r1:8b (distill); "+I,["ollama_local"]),
 L("gemma4:e4b","Gemma 4 E4B (local)","Google","Gemma 4","e4b","Current (local)","Local General",
   (6.0,5.6,5.2,5.8),(5.6,5.0,5.2,5.6),(5.6,5.2,4.6,5.8),(6.2,6.2,6.0,6.4),(5.6,5.0,6.0,5.8),("","",""),
   (8.4,8.0,4,9.8),(6.2,6.4,6.4,6.2),"128K",None,None,None,"low",3,3,
   "Efficient local Google model; offline/free","",
   "L","Local Ollama gemma4:e4b; "+I,["ollama_local"]),
 L("gemma4:12b","Gemma 4 12B (local QAT)","Google","Gemma 4","12b-it-qat","Current (local, QAT)","Local Reasoning/Multimodal",
   (7.7,7.8,7.4,7.5),(7.2,7.0,7.1,7.2),(7.1,6.9,5.8,7.3),(7.4,7.5,7.3,7.8),(7.4,6.9,7.3,7.2),(7.5,7.1,6.8),
   (7.2,7.0,9,9.8),(7.3,7.4,7.2,7.2),"256K",None,None,None,"medium",4,3,
   "Laptop-local multi-step reasoning, code helper, tool-capable local agent, text/image/audio analysis","Frontier-high-stakes autonomous actions; long production code rewrites without review",
   "M","Google model card: 11.95B, 256K, MMLU Pro 77.2, AIME 77.5, LiveCodeBench 72.0, GPQA 78.8; pulled exact Ollama QAT tag gemma4:12b-it-qat and copied local alias gemma4:12b; ollama show: completion/vision/audio/tools/thinking; local /api/chat smoke passed with top-level think=false; "+B,["ollama_local"]),
 L("gemma4:e2b","Gemma 4 E2B (local)","Google","Gemma 4","e2b","Current (local)","Local (tiny)",
   (5.2,4.8,4.4,5.0),(4.8,4.2,4.4,4.8),(4.8,4.4,3.8,5.0),(5.4,5.4,5.2,5.6),(4.8,4.2,5.2,5.0),("","",""),
   (9.0,8.6,4,9.9),(5.4,5.6,5.6,5.4),"128K",None,None,None,"low",2,2,
   "Tiny local Google model; offline/free","Depth",
   "L","Local Ollama gemma4:e2b; "+I,["ollama_local"]),
 L("deepseek-coder-v2","DeepSeek Coder V2 (local)","DeepSeek","DeepSeek Coder","v2","Current (local)","Local Coder",
   (5.8,5.2,5.0,5.6),(6.8,5.8,6.2,6.6),(6.0,5.6,4.6,6.0),(5.6,5.8,5.6,5.8),(5.8,5.2,6.2,6.0),("","",""),
   (7.8,7.4,5,9.8),(6.0,6.2,6.0,6.0),"128K",None,None,None,"low",3,3,
   "Local code completion; offline/free","Large-repo agentic SWE",
   "L","Local Ollama deepseek-coder-v2; "+I,["ollama_local"]),
 L("qwen2.5-coder:3b","Qwen2.5 Coder 3B (local)","Alibaba","Qwen2.5 Coder","3b","Current (local)","Local Coder (tiny)",
   (5.2,4.6,4.4,5.0),(6.2,5.2,5.6,6.0),(5.2,4.8,4.0,5.2),(5.2,5.2,5.2,5.4),(5.2,4.6,5.8,5.4),("","",""),
   (8.6,8.2,5,9.9),(5.6,5.8,5.6,5.6),"128K",None,None,None,"low",2,2,
   "Tiny local code helper; offline/free","Anything complex",
   "L","Local Ollama qwen2.5-coder:3b; "+I,["ollama_local"]),
 L("ministral-3:3b","Ministral 3 3B (local)","Mistral","Ministral 3","3b","Current (local)","Local Edge (tiny)",
   (5.4,4.8,4.6,5.2),(5.2,4.4,4.6,5.0),(5.2,4.8,4.0,5.2),(5.6,5.6,5.4,5.8),(5.0,4.4,5.4,5.2),("","",""),
   (8.8,8.4,5,9.9),(5.6,5.8,5.8,5.6),"128K",None,None,None,"low",2,2,
   "Tiny local edge chat; offline/free","Depth",
   "L","Local Ollama ministral-3:3b; "+I,["ollama_local"]),
 L("lfm2.5-thinking:1.2b","LFM2.5 Thinking 1.2B (local)","Liquid AI","LFM2.5","1.2b","Current (local)","Local Reasoning (tiny)",
   (5.4,5.2,4.2,4.6),(4.6,4.0,4.2,4.6),(4.6,4.2,3.6,4.8),(4.8,4.8,4.8,4.8),(4.8,4.4,5.0,5.2),("","",""),
   (9.0,8.8,4,9.9),(5.0,5.2,5.0,5.0),"32K",None,None,None,"low",2,2,
   "Tiny local reasoning; offline/free","Most real tasks",
   "L","Local Ollama lfm2.5-thinking:1.2b; "+I,["ollama_local"]),
 L("qwen3.5:4b","Qwen3.5 4B (local)","Alibaba","Qwen3.5","4b","Current (local)","Local (tiny)",
   (5.4,5.0,4.6,5.2),(5.2,4.6,4.8,5.2),(5.0,4.6,4.0,5.2),(5.4,5.4,5.2,5.8),(5.0,4.4,5.4,5.2),("","",""),
   (8.8,8.4,5,9.9),(5.6,5.8,5.8,5.6),"128K",None,None,None,"low",2,2,
   "Small local general; offline/free","Depth",
   "L","Local Ollama qwen3.5:4b; "+I,["ollama_local"]),
 L("qwen3.5:2b","Qwen3.5 2B (local)","Alibaba","Qwen3.5","2b","Current (local)","Local (tiny)",
   (4.8,4.4,4.0,4.6),(4.6,4.0,4.2,4.6),(4.4,4.0,3.4,4.6),(4.8,4.8,4.6,5.2),(4.4,3.8,4.8,4.6),("","",""),
   (9.2,8.8,5,9.9),(5.0,5.2,5.2,5.0),"128K",None,None,None,"low",2,2,
   "Very small local; quick offline tasks","",
   "L","Local Ollama qwen3.5:2b; "+I,["ollama_local"]),
 L("qwen3.5:0.8b","Qwen3.5 0.8B (local)","Alibaba","Qwen3.5","0.8b","Current (local)","Local (nano)",
   (3.8,3.4,3.0,3.6),(3.6,3.0,3.2,3.6),(3.4,3.0,2.6,3.6),(4.0,4.0,3.8,4.2),(3.6,3.2,4.0,3.8),("","",""),
   (9.4,9.2,5,10),(4.2,4.4,4.4,4.2),"128K",None,None,None,"low",1,1,
   "Nano local; quick smoke tests offline","Real work",
   "L","Local Ollama qwen3.5:0.8b; "+I,["ollama_local"]),
 L("qwen3:4b","Qwen3 4B (local)","Alibaba","Qwen3","4b","Current (local)","Local (tiny)",
   (5.2,4.8,4.4,5.0),(5.0,4.4,4.6,5.0),(4.8,4.4,3.8,5.0),(5.2,5.2,5.0,5.6),(4.8,4.2,5.2,5.0),("","",""),
   (8.8,8.4,5,9.9),(5.4,5.6,5.6,5.4),"128K",None,None,None,"low",2,2,
   "Small local general; offline/free","Depth",
   "L","Local Ollama qwen3:4b; "+I,["ollama_local"]),
 L("qwen2.5:0.5b","Qwen2.5 0.5B (local)","Alibaba","Qwen2.5","0.5b","Current (local)","Local (nano)",
   (3.4,3.0,2.6,3.2),(3.2,2.6,2.8,3.2),(3.0,2.6,2.2,3.2),(3.6,3.6,3.4,3.8),(3.2,2.8,3.6,3.4),("","",""),
   (9.6,9.4,4,10),(3.8,4.0,4.2,3.8),"32K",None,None,None,"low",1,1,
   "Nano local; smoke-tests, Hermes default","Real work",
   "L","Local Ollama qwen2.5:0.5b (Hermes primary); "+I,["ollama_local"]),
 L("mai-thinking-1","MAI-Thinking-1","Microsoft","MAI","thinking-1","Current (new 2026-06-02)","Reasoning Flagship",
   (9.0,9.5,8.6,8.4),(8.6,8.2,8.2,8.6),(8.4,8.4,8.0,8.6),(8.0,8.0,7.8,8.0),(8.2,7.6,8.4,8.8),("","",""),
   (7.0,7.0,8,7.5),(8.6,8.4,8.4,8.8),"256K",None,None,None,"high",5,4,
   "In-house reasoning/math (no OpenAI distill); Copilot-native reasoning","Multimodal; ultra-low-latency",
   "H","AIME 2025 97.0% / 2026 94.5%; matches Opus 4.6 SWE-bench Pro; beats Sonnet 4.6 blind; 35B-active MoE; "+B,["github_models","azure","copilot"]),
 L("mai-code-1-flash","MAI-Code-1-Flash","Microsoft","MAI","code-1-flash","Current (new 2026-06-02)","Coder (fast/cheap)",
   (6.0,5.8,5.4,6.0),(7.0,5.1,6.2,7.2),(7.0,6.4,6.0,8.0),(5.8,6.0,6.0,5.8),(6.0,5.4,6.6,6.0),("","",""),
   (9.0,9.0,6,9.5),(7.0,7.2,7.4,7.0),"128K",None,None,None,"medium",4,3,
   "Copilot/VS Code-native fast coding; cheaper than Haiku 4.5","Deep reasoning; long-horizon autonomy",
   "H","SWE-bench Pro 51.2% (vs Haiku 35.2%); +28.9 IFBench vs Haiku; 5B; "+B,["copilot","github_models","azure"]),
 L("minimax-m3","MiniMax M3","MiniMax","MiniMax M3","3","Current (new 2026-06)","Open (agentic/coding)",
   (8.4,8.0,8.0,7.8),(8.4,8.0,8.0,8.4),(8.6,8.2,8.0,8.4),(8.0,8.0,7.8,8.0),(7.8,7.0,8.0,8.2),("","",""),
   (7.6,7.6,8,8.6),(7.6,7.6,7.4,7.6),"1M",None,None,None,"high",4,4,
   "Newest MiniMax MoE; agentic/coding, long context","",
   "M","Released June 2026; benchmarks still settling; "+I,["ollama_cloud","together","openrouter"]),
 L("gpt-5.4-pro","GPT-5.4 Pro","OpenAI","GPT-5.x","5.4-pro","Current","Flagship (max compute)",
   (9.3,9.1,9.1,8.9),(9.0,8.7,8.6,8.9),(9.1,9.1,8.7,9.1),(8.7,8.7,8.6,8.5),(8.6,7.8,8.7,9.0),(9.2,9.0,""),
   (4.5,4.0,10,3.5),(8.6,8.6,8.7,8.5),"1M",128000,None,None,"critical",5,5,
   "Extended-compute GPT-5.4 for hardest tasks","Latency/cost-sensitive",
   "M","Pro variant of GPT-5.4 (provider-doc); "+I,["openai_api","codex_cli"]),
 L("gpt-5.4-mini","GPT-5.4 Mini","OpenAI","GPT-5.x","5.4-mini","Current","Fast-Cheap",
   (8.0,7.6,7.4,7.8),(8.0,7.4,7.4,7.8),(8.0,7.6,7.2,8.0),(7.8,7.8,7.8,7.6),(7.4,6.6,7.8,7.4),(7.8,7.8,""),
   (8.8,8.8,9,8.2),(8.0,8.0,8.2,7.8),"400K",64000,None,None,"high",4,4,
   "Near-frontier at low latency/cost; ChatGPT Thinking fallback","Hardest reasoning",
   "M","GPT-5.4 mini (provider-doc); "+I,["openai_api","codex_cli","github_models"]),
 L("gpt-5.4-nano","GPT-5.4 Nano","OpenAI","GPT-5.x","5.4-nano","Current","Fast-Cheap",
   (7.0,6.4,6.2,6.8),(7.0,6.2,6.2,6.8),(7.0,6.4,5.8,7.0),(7.0,7.0,7.0,6.8),(6.4,5.6,7.0,6.4),(6.8,6.8,""),
   (9.4,9.4,8,8.8),(7.2,7.4,8.0,7.2),"400K",64000,None,None,"medium",3,3,
   "Cheapest/fastest GPT-5.4 for simple tasks","Depth",
   "M","GPT-5.4 nano (provider-doc); "+I,["openai_api","github_models"]),
]

EMB_STATS=["Embed Quality (MTEB)","Multilingual","Retrieval","Speed","Cost-Efficiency"]
EMBED=[
 ("text-embedding-3-large","Text Embedding 3 Large","OpenAI","Current",[7.8,7.4,7.8,8.0,7.0],"3072 dims","8191 tok","M","MTEB ~64.6; OpenAI docs",["openai_api","azure"]),
 ("text-embedding-3-small","Text Embedding 3 Small","OpenAI","Current",[7.0,6.8,7.0,9.0,9.0],"1536 dims","8191 tok","M","Cheaper OAI embed; MTEB ~62",["openai_api"]),
 ("embed-multilingual-v3.0","Cohere Embed v3 Multilingual","Cohere","Current",[7.6,8.6,7.8,8.0,7.5],"1024 dims","512 tok","M","100+ langs; MTEB-strong",["cohere_api","bedrock"]),
 ("gemini-embedding-2","Gemini Embedding 2","Google","Current",[9.0,8.8,9.0,7.8,7.2],"3072 dims (MRL 1536/768 supported)","8192 tok + multimodal","H","Google Developers Blog: GA via Gemini API; text/image/video/audio/PDF unified embedding space",["gemini_api"]),
 ("qwen3-embedding:4b","Qwen3 Embedding 4B (local)","Alibaba","Current",[8.2,8.0,8.2,7.0,9.6],"2560 dims","32K tok","M","Qwen3-Embedding (8B MTEB ~70); local on Dell",["ollama_local"]),
 ("qwen3-embedding:0.6b","Qwen3 Embedding 0.6B (local)","Alibaba","Current",[7.2,7.0,7.2,8.5,9.8],"1024 dims","32K tok","L","Tiny local embed",["ollama_local"]),
 ("nomic-embed-text","Nomic Embed Text (local)","Nomic","Current",[6.8,6.2,6.8,8.5,9.6],"768 dims","8192 tok","M","Open local embed; MTEB ~62",["ollama_local"]),
 ("codestral-embed-25-05","Codestral Embed","Mistral","Current",[7.2,6.5,7.6,8.0,7.5],"1536 dims","8K tok","L","Code-specialized embeddings",["mistral_api"]),
]
IMG_STATS=["Prompt Adherence","Text Rendering","Photorealism","Style Range","Speed","Cost-Efficiency"]
IMAGE=[
 ("gpt-image-1.5","GPT Image 1.5","OpenAI","Legacy (prior)",[9.0,9.0,8.6,8.6,6.5,5.5],"High res","","H","Image Arena Elo 1244 (#2-3); arena 270",["openai_api"]),
 ("gpt-image-1","GPT Image 1","OpenAI","Legacy",[8.4,8.4,8.0,8.2,6.8,6.0],"High res","","M","Prior OAI image; "+I,["openai_api"]),
 ("imagen-4.0","Google Imagen 4.0","Google","Legacy (prior)",[8.6,8.0,8.8,8.2,7.5,7.0],"2K","","M","Imagen 4 (arena mid-high)",["together","gemini_api"]),
 ("black-forest-labs/FLUX.1-dev","FLUX 2 Dev","Black Forest Labs","Current",[8.4,7.6,8.6,8.4,6.5,9.0],"2K","open-weight","M","FLUX 2 Dev Elo 1150 (open)",["together","fireworks"]),
 ("black-forest-labs/FLUX.1-schnell","FLUX 2 Schnell","Black Forest Labs","Current",[7.8,7.0,8.0,8.0,9.0,9.5],"1K","open-weight","M","Fast open FLUX",["together","fireworks"]),
 ("stabilityai/stable-diffusion-3-5-large","Stable Diffusion 3.5 Large","Stability AI","Current",[7.6,6.8,8.0,8.6,7.0,8.5],"1K","open-weight","L","SD3.5L (open)",["together","bedrock"]),
 ("ideogram-ai/ideogram-v2","Ideogram V2","Ideogram","Current",[8.2,9.2,7.8,8.0,7.0,7.5],"1K","","L","Best-in-class text rendering",["together"]),
 ("grok-imagine-image","Grok Imagine","xAI","Current",[7.8,7.2,8.0,7.8,7.5,6.0],"1K","","L","xAI image gen",["xai_api"]),
 ("mai-image-2.5","MAI-Image-2.5","Microsoft","Current (new 2026-06-02)",[9.0,8.8,8.8,8.6,7.0,7.0],"High res","","M","Surpasses Nano Banana Pro arena (Microsoft); "+I,["azure","copilot"]),
 ("mai-image-2.5-flash","MAI-Image-2.5 Flash","Microsoft","Current (new 2026-06-02)",[8.6,8.4,8.4,8.4,9.2,9.0],"High res","ultra-efficient","M","Fast MAI image variant; "+I,["azure","copilot"]),
 ("gpt-image-2","GPT Image 2 (ChatGPT Images 2.0)","OpenAI","Current (new 2026)",[9.4,9.4,8.8,8.8,6.5,5.5],"High res","","H","Image Arena #1 debut (surpassed Nano Banana 2 / FLUX.2); provider-doc + arena",["openai_api"]),
 ("gemini-3-pro-image","Nano Banana Pro (Gemini 3 Pro Image)","Google","Current (GA 2026-05-28)",[9.0,9.4,8.8,8.8,6.5,6.5],"High res","typography-focused","H","GA 2026-05-28 (blog.google); high-quality complex typography",["gemini_api","together"]),
 ("gemini-3.1-flash-image","Nano Banana 2 (Gemini 3.1 Flash Image)","Google","Current (GA 2026-05-28)",[9.2,8.8,8.6,8.4,9.0,8.5],"High res","fast/throughput","H","GA 2026-05-28; Arena Elo ~1266 (top); video-to-image context",["gemini_api","together"]),
]
VID_STATS=["Motion Coherence","Prompt Adherence","Audio Sync","Realism","Duration","Cost-Efficiency"]
VIDEO=[
 ("sora-2-pro","Sora 2 Pro","OpenAI","Current",[9.0,8.8,9.0,8.8,7.5,4.5],"~60s","synced audio","M","Most advanced synced-audio video",["openai_api"]),
 ("sora-2","Sora 2","OpenAI","Current",[8.6,8.4,8.6,8.4,7.0,5.5],"~30s","synced audio","M","Flagship video+audio",["openai_api"]),
 ("veo-3.0","Google Veo 3.0","Google","Legacy (prior)",[8.8,8.6,8.8,8.8,7.0,6.0],"~30s","synced audio","M","Veo 3 (Vertex/Together)",["together","gemini_api"]),
 ("kling-2.1","Kling 2.1","Kuaishou","Current",[8.4,8.0,7.0,8.4,7.0,7.0],"~10s","","L","Strong motion realism",["together"]),
 ("minimax/video-01","MiniMax Hailuo","MiniMax","Current",[8.0,7.8,6.5,8.0,6.0,7.5],"~6s","","L","Hailuo video",["together"]),
 ("luma.ray-v2:0","Luma Ray 2","Luma AI","Current",[8.0,7.8,6.0,8.0,6.0,7.0],"~9s","","L","Bedrock video gen",["bedrock"]),
 ("grok-imagine-video","Grok Imagine Video","xAI","Current",[7.8,7.4,7.5,7.6,5.0,6.0],"~6s","synced audio","L","xAI video gen",["xai_api"]),
 ("veo-3.1","Google Veo 3.1 (Lite Preview)","Google","Current (2026)",[8.6,8.6,8.6,8.6,7.0,7.5],"~30s","synced audio; cost-efficient","M","veo-3.1-lite-generate-preview (most cost-efficient video); provider-doc",["together","gemini_api"]),
]
AUD_STATS=["Accuracy (WER inv)","Naturalness (TTS)","Latency","Languages","Speed","Cost-Efficiency"]
AUDIO=[
 ("gpt-realtime-1.5","GPT Realtime 1.5","OpenAI","Legacy (prior)",[8.8,9.0,9.2,8.5,9.0,5.5],"STT+TTS realtime","audio in/out","M","Best realtime voice",["openai_api"]),
 ("whisper-1","Whisper","OpenAI","Current",[8.6,0.0,7.0,9.0,8.0,8.5],"STT","99 langs","H","General-purpose ASR, widely benchmarked",["openai_api"]),
 ("gpt-4o-transcribe","GPT-4o Transcribe","OpenAI","Current",[8.8,0.0,7.5,9.0,8.0,7.0],"STT","multi","M","GPT-4o-powered ASR",["openai_api"]),
 ("gpt-4o-mini-tts","GPT-4o Mini TTS","OpenAI","Current",[0.0,8.6,8.5,8.0,9.0,8.0],"TTS","multi","M","Steerable TTS",["openai_api"]),
 ("voxtral-small-25-07","Voxtral Small","Mistral","Current",[8.0,7.5,7.5,8.0,8.0,8.0],"STT+audio","multi","L","Mistral audio model",["mistral_api"]),
 ("amazon.nova-2-sonic","Amazon Nova 2 Sonic","Amazon","Current",[8.2,8.4,8.6,7.5,8.5,7.5],"realtime","conversational","L","Bedrock conversational audio",["bedrock"]),
 ("mai-transcribe-1.5","MAI-Transcribe-1.5","Microsoft","Current (new 2026-06-02)",[9.2,0.0,8.0,8.5,8.5,8.0],"STT","SOTA accuracy claim","M","Microsoft best-in-class transcription; "+I,["azure","copilot"]),
 ("mai-voice-2","MAI-Voice-2","Microsoft","Current (new 2026-06-02)",[0.0,8.8,8.5,8.0,8.5,8.0],"TTS","15 langs, voice cloning","M","Natural TTS, voice adaptation from short sample; "+I,["azure","copilot"]),
 ("gpt-realtime-2","GPT Realtime 2 (reason+translate+transcribe)","OpenAI","Current (2026)",[9.0,9.0,9.2,9.0,9.0,5.5],"realtime","reason/translate/transcribe","M","New OpenAI realtime voice family (provider-doc June 2026)",["openai_api"]),
 ("gemini-3.1-flash-live","Gemini 3.1 Flash Live","Google","Current (2026)",[8.6,8.8,9.0,8.5,9.0,7.5],"realtime","audio-to-audio dialogue","M","Real-time voice-first model (provider-doc)",["gemini_api"]),
 ("lyria-3","Lyria 3 (music gen)","Google","Current (2026)",[0.0,8.6,7.0,0.0,7.0,7.0],"Music gen","clip + full-song","M","lyria-3-clip-preview / lyria-3-pro-preview (provider-doc)",["gemini_api"]),
]
VIS_STATS=["OCR Accuracy","Doc Understanding","Layout/Chart","Extraction","Speed","Cost-Efficiency"]
VISION=[
 ("ocr-3-25-12","Mistral OCR 3","Mistral","Current",[8.6,8.0,8.2,8.0,8.0,8.0],"document OCR","","M","Mistral OCR latest",["mistral_api"]),
 ("glm-ocr","GLM-OCR (local)","Zhipu AI","Current",[8.0,7.6,7.8,7.6,7.0,9.6],"document OCR","local","L","Local OCR on Dell",["ollama_local"]),
 ("deepseek-vl2","DeepSeek VL2","DeepSeek","Current",[7.8,7.8,7.6,7.6,7.0,9.0],"vision-language","","L","Doc understanding/OCR",["deepseek_api"]),
 ("qwen2.5vl:3b","Qwen2.5 VL 3B (local)","Alibaba","Current",[7.4,7.0,7.0,7.2,7.5,9.6],"vision-language","local","L","Local VL on Dell",["ollama_local"]),
 ("qwen3-vl:2b","Qwen3 VL 2B (local)","Alibaba","Current",[7.2,6.8,6.8,7.0,7.8,9.7],"vision-language","local","L","Local VL on Dell",["ollama_local"]),
 ("nuextract","NuExtract (local)","NuMind","Current",[6.5,6.0,5.5,8.4,8.0,9.6],"structured extraction","local","L","Local structured extraction",["ollama_local"]),
 ("twelvelabs.pegasus-1-v1:0","TwelveLabs Pegasus 1","TwelveLabs","Current",[0.0,8.2,7.5,7.8,7.0,6.5],"video understanding","","L","Bedrock video understanding",["bedrock"]),
]
MOD_STATS=["Accuracy","Coverage (text/img)","Latency","Cost-Efficiency"]
MOD=[
 ("omni-moderation-latest","Omni Moderation","OpenAI","Current",[8.4,9.0,9.0,9.5],"text+image","","M","OpenAI omni moderation (free)",["openai_api"]),
 ("mistral-moderation-26-03","Mistral Moderation","Mistral","Current",[8.0,7.0,8.8,9.0],"text","","L","Mistral moderation",["mistral_api"]),
]

HDR_FILL=PatternFill("solid",fgColor="1F2A44"); HDR_FONT=Font(name="Arial",bold=True,color="FFFFFF",size=10)
GRP_FILL=PatternFill("solid",fgColor="2E4374"); GRP_FONT=Font(name="Arial",bold=True,color="FFFFFF",size=10)
BASE_FONT=Font(name="Arial",size=10); THIN=Side(style="thin",color="D9D9D9"); BORDER=Border(left=THIN,right=THIN,top=THIN,bottom=THIN)
CONF_FILL={"H":PatternFill("solid",fgColor="C6EFCE"),"M":PatternFill("solid",fgColor="FFEB9C"),"L":PatternFill("solid",fgColor="FFC7CE")}
def color_scale():
    return ColorScaleRule(start_type="num",start_value=0,start_color="F8696B",
        mid_type="num",mid_value=5,mid_color="FFEB84",end_type="num",end_value=10,end_color="63BE7B")
def comp(vals):
    nums=[v for v in vals if isinstance(v,(int,float)) and v!="" ]
    return round(sum(nums)/len(nums),1) if nums else ""
def style_header(ws,row,ncols):
    for c in range(1,ncols+1):
        cell=ws.cell(row=row,column=c); cell.fill=HDR_FILL; cell.font=HDR_FONT
        cell.alignment=Alignment(horizontal="center",vertical="center",wrap_text=True); cell.border=BORDER

SOURCES=[
 ("Provider specs","OpenAI model docs","developers.openai.com/api/docs/models","OpenAI IDs, context, max output, pricing, modalities, tool support","5 - authoritative for OpenAI attributes","OpenAI rows and image/audio/current status"),
 ("Provider specs","Anthropic models overview","platform.claude.com/docs/.../models/overview","Claude current model IDs, context, pricing, max output, cloud surfaces","5 - authoritative for Claude attributes","Claude rows and Bedrock/Vertex/Foundry availability"),
 ("Provider specs","Google Gemini model cards","ai.google.dev/gemini-api/docs/models","Gemini text/image/video/audio/embedding IDs, limits, status, last update","5 - authoritative for Gemini attributes","Gemini/Gemini image/Veo/Lyria/audio/embedding rows"),
 ("Provider specs","Google Gemma 4 model card","ai.google.dev/gemma/docs/core/model_card_4","Gemma 4 12B/E2B/E4B/26B/31B parameters, context, modalities, official benchmark table","5 - authoritative for Gemma family attributes","Gemma 4 local rows and rating anchors"),
 ("Provider specs","Ollama Gemma 4 tags","ollama.com/library/gemma4/tags","Ollama tag names, QAT availability, local artifact size, context, supported Ollama input modalities","5 - authoritative for Ollama local tag facts","Gemma 4 local tag IDs and install targets"),
 ("Provider specs","xAI model docs","docs.x.ai/developers/models","Grok chat/coding/image/video/voice model families and alias rules","5 - authoritative for xAI attributes","Grok rows and status"),
 ("Provider specs","DeepSeek API docs/changelog","api-docs.deepseek.com","DeepSeek V4 Pro/Flash IDs and legacy alias retirement","5 - authoritative for DeepSeek attributes","DeepSeek rows and alias notes"),
 ("Provider specs","Mistral model overview","docs.mistral.ai/models/overview","Mistral Medium/Small/Devstral/OCR/Voxtral/Codestral/Moderation models","5 - authoritative for Mistral attributes","Mistral rows across LLM/audio/OCR/moderation"),
 ("Provider specs","Cohere model overview","docs.cohere.com/v2/docs/models","Command A+, Command A Vision, Embed, Rerank, Transcribe families","5 - authoritative for Cohere attributes","Cohere LLM/embed/rerank/vision rows"),
 ("Provider specs","Alibaba Model Studio docs","help.aliyun.com/zh/model-studio","Qwen3.7/Qwen3.6 recommended models, context, output, tools","5 - authoritative for DashScope/Qwen API attributes","Qwen cloud rows"),
 ("Provider specs","Meta Llama 4 launch","ai.meta.com/blog/llama-4-multimodal-intelligence","Llama 4 Scout/Maverick official parameters, experts, context, multimodal status","5 - authoritative for Meta release facts","Llama 4 rows"),
 ("Provider specs","Amazon Bedrock model cards","docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html","Bedrock supported model families and IDs across providers","4 - authoritative for Bedrock surface availability","Surface coverage and Bedrock model rows"),
 ("Provider specs","Microsoft AI models","microsoft.ai/models and model cards","MAI Thinking, Code, Image, Transcribe, Voice model family facts","5 - authoritative for Microsoft MAI attributes","MAI rows; preview/private status checked before routing"),
 ("Provider specs","MiniMax M3 official launch","minimax.io/blog/minimax-m3","MiniMax M3 1M context, multimodal, agentic/coding release facts","5 - authoritative for MiniMax M3 attributes","MiniMax rows"),
 ("Benchmark","SWE-bench leaderboards","swebench.com","Software-engineering task benchmark signals","4 - benchmark owner, but run configs matter","Coding/agentic SWE routing scores"),
 ("Benchmark","Artificial Analysis","artificialanalysis.ai","Composite intelligence, price, speed, image arena and model summaries","3 - independent tracker, not provider law","Relative 0-10 routing estimates"),
 ("Benchmark","Epoch AI FrontierMath","epoch.ai/benchmarks/frontiermath","Advanced math/reasoning benchmark context","4 - benchmark owner for FrontierMath","Reasoning/math score confidence"),
 ("Benchmark","LMArena / Arena AI","lmarena.ai","Human preference arena for chat and image models","3 - useful but prompt/user-mix dependent","Creative/chat/image quality tie-breaks"),
 ("Benchmark","MTEB","huggingface.co/spaces/mteb/leaderboard","Embedding retrieval/classification quality benchmark","4 - benchmark ecosystem source","Embedding quality and retrieval stats"),
 ("Runtime/local","archive/AI_PROVIDER_MODEL_ROSTER_2026-06-06_SUPERSEDED_by_2026-06-07.md","local repo artifact","On-Dell access, installed surfaces, auth/billing state from runtime scan","5 for local access on scan date; can drift","Surface auth/billing and local model reachability"),
]

wb=Workbook(); wb.remove(wb.active)

# ============ LLM SHEET ============
ws=wb.create_sheet("01 LLMs")
meta_cols=["Model ID","Model Name","Provider","Family/Gen","Version","Status","Archetype",
 "Routing Surface","Surface Type","Auth on Dell","Billing Class","Runs Local on Dell"]
flat=[]
for c in meta_cols: flat.append(("meta",c,None))
for gname,subs in LLM_GROUPS:
    for s in subs: flat.append(("stat",s,gname))
    flat.append(("comp",gname+" ★",gname))
flat.append(("pwr","POWER LEVEL",None))
for c in ["Context Window","Max Output","$/MTok In","$/MTok Out","consequence_max","rating_instruction","rating_quality","Recommended Roles","Avoid For","Confidence","Sources","Last Verified"]:
    flat.append(("meta2",c,None))
ncols=len(flat)
for i,(kind,label,grp) in enumerate(flat,1):
    ws.cell(row=2,column=i,value=label)
i=1
while i<=ncols:
    kind,label,grp=flat[i-1]
    if kind in("stat","comp") and grp:
        j=i
        while j<=ncols and flat[j-1][2]==grp and flat[j-1][0] in("stat","comp"): j+=1
        ws.merge_cells(start_row=1,start_column=i,end_row=1,end_column=j-1)
        gc=ws.cell(row=1,column=i,value=grp); gc.fill=GRP_FILL; gc.font=GRP_FONT
        gc.alignment=Alignment(horizontal="center",vertical="center")
        i=j
    else:
        ws.cell(row=1,column=i,value=""); i+=1
style_header(ws,2,ncols)
def idx(label):
    for n,(k,l,g) in enumerate(flat,1):
        if l==label: return n
    return None
group_substat_cols={g:[] for g,_ in LLM_GROUPS}
for n,(k,l,g) in enumerate(flat,1):
    if k=="stat": group_substat_cols[g].append(n)
comp_col={}
for n,(k,l,g) in enumerate(flat,1):
    if k=="comp": comp_col[g]=n
pwr_col=idx("POWER LEVEL")
r=3; csv_rows=[[l for _,l,_ in flat]]
for m in LLMS:
    statmap={"Research & Reasoning":m["rea"],"Coding & Engineering":m["cod"],"Agentic & Tool Calling":m["ag"],
     "Creative & Language":m["cr"],"Predictive & Analytical":m["pr"],"Multimodal":m["mm"],
     "Performance & Economy":m["pf"],"Reliability & Safety":m["rl"]}
    for sc in m["surf"]:
        lbl,styp,auth,bill,loc=SURF[sc]
        rowvals=[m["mid"],m["name"],m["prov"],m["fam"],m["ver"],m["status"],m["arch"],lbl,styp,auth,bill,loc]
        for gname,subs in LLM_GROUPS:
            gv=statmap[gname]
            for k2 in range(len(subs)):
                rowvals.append(gv[k2] if k2<len(gv) and gv[k2]!="" else "")
            rowvals.append(comp(gv))
        present=[(PWR_WEIGHTS[g],comp(statmap[g])) for g in PWR_WEIGHTS if comp(statmap[g])!=""]
        pw=round(sum(w*v for w,v in present)/sum(w for w,_ in present),1) if present else ""
        rowvals.append(pw)
        rowvals += [m["ctx"],m["mx"],m["pin"] if m["pin"] is not None else "",m["pout"] if m["pout"] is not None else "",
                    m["cons"],m["ri"],m["rq"],m["roles"],m["avoid"],m["conf"],m["src"],m["lv"]]
        csv_rows.append(rowvals)
        for c,v in enumerate(rowvals,1):
            ws.cell(row=r,column=c,value=v)
        for gname in comp_col:
            cols=group_substat_cols[gname]
            a=get_column_letter(cols[0]); b=get_column_letter(cols[-1])
            ws.cell(row=r,column=comp_col[gname]).value=f'=IFERROR(ROUND(AVERAGE({a}{r}:{b}{r}),1),"")'
        terms=[f'{w}*N({get_column_letter(comp_col[g])}{r})' for g,w in PWR_WEIGHTS.items()]
        ws.cell(row=r,column=pwr_col).value=f'=ROUND({"+".join(terms)},1)'
        ws.cell(row=r,column=idx("Confidence")).fill=CONF_FILL.get(m["conf"],PatternFill())
        r+=1
for row in ws.iter_rows(min_row=3,max_row=r-1,min_col=1,max_col=ncols):
    for cell in row:
        cell.font=BASE_FONT; cell.border=BORDER; cell.alignment=Alignment(vertical="center")
ws.freeze_panes="C3"
ws.column_dimensions["A"].width=30; ws.column_dimensions["B"].width=24
for g in group_substat_cols:
    for cidx in group_substat_cols[g]:
        ws.column_dimensions[get_column_letter(cidx)].width=11
        ws.conditional_formatting.add(f"{get_column_letter(cidx)}3:{get_column_letter(cidx)}{r-1}",color_scale())
for g in comp_col:
    ws.column_dimensions[get_column_letter(comp_col[g])].width=12
    ws.conditional_formatting.add(f"{get_column_letter(comp_col[g])}3:{get_column_letter(comp_col[g])}{r-1}",color_scale())
ws.column_dimensions[get_column_letter(pwr_col)].width=12
ws.conditional_formatting.add(f"{get_column_letter(pwr_col)}3:{get_column_letter(pwr_col)}{r-1}",color_scale())
for lab in ["Recommended Roles","Avoid For","Sources"]:
    ws.column_dimensions[get_column_letter(idx(lab))].width=42
ws.row_dimensions[1].height=20; ws.row_dimensions[2].height=44
with open(os.path.join(CSVDIR,"01_LLMs.csv"),"w",newline="",encoding="utf-8") as f:
    csv.writer(f).writerows(csv_rows)

def build_type_sheet(sheetname, stat_labels, data, extra_headers, csvname):
    ws=wb.create_sheet(sheetname)
    meta=["Model ID","Model Name","Provider","Status","Routing Surface","Surface Type","Auth on Dell","Billing Class","Runs Local"]
    flat=[("meta",c) for c in meta]+[("stat",s) for s in stat_labels]+[("comp","OVERALL ★")]+[("meta2",e) for e in extra_headers]+[("meta2",x) for x in ["Confidence","Sources","Last Verified"]]
    ncols=len(flat)
    for i,(k,l) in enumerate(flat,1): ws.cell(row=2,column=i,value=l)
    s0=len(meta)+1; s1=len(meta)+len(stat_labels)
    ws.merge_cells(start_row=1,start_column=s0,end_row=1,end_column=s1)
    gc=ws.cell(row=1,column=s0,value="Quality Stats (0-10)"); gc.fill=GRP_FILL; gc.font=GRP_FONT; gc.alignment=Alignment(horizontal="center")
    style_header(ws,2,ncols)
    stat_cols=list(range(s0,s1+1)); comp_c=s1+1
    r=3; csvrows=[[l for _,l in flat]]
    for d in data:
        mid,name,prov,status,stats,e1,e2,conf,src,surfaces=d
        for sc in surfaces:
            lbl,styp,auth,bill,loc=SURF[sc]
            base=[mid,name,prov,status,lbl,styp,auth,bill,loc]
            rowv=base+[(v if v!=0.0 else "") for v in stats]+[comp(stats)]+[e1,e2,conf,src,"2026-06-07"]
            csvrows.append(base+[*stats,comp(stats),e1,e2,conf,src,"2026-06-07"])
            for c,v in enumerate(rowv,1): ws.cell(row=r,column=c,value=v)
            a=get_column_letter(s0); b=get_column_letter(s1)
            ws.cell(row=r,column=comp_c).value=f'=IFERROR(ROUND(AVERAGE({a}{r}:{b}{r}),1),"")'
            ws.cell(row=r,column=ncols-2).fill=CONF_FILL.get(conf,PatternFill())
            r+=1
    for row in ws.iter_rows(min_row=3,max_row=r-1,min_col=1,max_col=ncols):
        for cell in row: cell.font=BASE_FONT; cell.border=BORDER; cell.alignment=Alignment(vertical="center")
    ws.freeze_panes="B3"; ws.column_dimensions["A"].width=30; ws.column_dimensions["B"].width=26
    for cidx in stat_cols+[comp_c]:
        ws.column_dimensions[get_column_letter(cidx)].width=13
        ws.conditional_formatting.add(f"{get_column_letter(cidx)}3:{get_column_letter(cidx)}{r-1}",color_scale())
    ws.column_dimensions[get_column_letter(ncols-1)].width=40
    ws.row_dimensions[2].height=42
    with open(os.path.join(CSVDIR,csvname),"w",newline="",encoding="utf-8") as f:
        csv.writer(f).writerows(csvrows)

build_type_sheet("02 Embeddings & Rerank",EMB_STATS,EMBED,["Dimensions","Max Input"],"02_Embeddings.csv")
build_type_sheet("03 Image Generation",IMG_STATS,IMAGE,["Max Res","License"],"03_Image.csv")
build_type_sheet("04 Video Generation",VID_STATS,VIDEO,["Max Duration","Audio"],"04_Video.csv")
build_type_sheet("05 Audio & Speech",AUD_STATS,AUDIO,["Subtype","Notes"],"05_Audio.csv")
build_type_sheet("06 Vision-OCR-Document",VIS_STATS,VISION,["Subtype","Notes"],"06_Vision.csv")
build_type_sheet("07 Moderation",MOD_STATS,MOD,["Coverage","Notes"],"07_Moderation.csv")

ws=wb.create_sheet("08 Surfaces & Access")
hdr=["Surface","Type","Auth on Dell (2026-06-06)","Billing Class","Runs Local","Routing Notes"]
for i,h in enumerate(hdr,1): ws.cell(row=1,column=i,value=h)
style_header(ws,1,len(hdr))
snotes={
 "claude_cli":"Primary high-stakes coding/analysis adapter (subprocess). Shared Anthropic pool.",
 "claude_desk":"This Cowork session. Skills + connectors.",
 "anthropic_api":"Subscription via Claude plan; no metered key.",
 "codex_cli":"ChatGPT-plan OAuth; default gpt-5.5 reasoning=high. No metered key.",
 "copilot":"GitHub premium-requests; default claude-haiku-4.5; --model switch.",
 "github_models":"AI Toolkit; gpt-5/gpt-4.1/DeepSeek-R1 configured; encrypted keys.",
 "gemini_cli":"Google OAuth re-authed 2026-06-06; free Code Assist tier.",
 "gemini_api":"Same Google auth; multimodal.",
 "antigravity":"Free agent platform; compute-budget refresh ~5h; Gemini+Claude+GPT-OSS.",
 "bedrock":"AWS Kiro -> Bedrock; Claude Sonnet 4.5 primary; login not on disk.",
 "azure":"AI Foundry installed, NOT credentialed; unusable until Azure sign-in.",
 "ollama_local":f"{ollama_local_model_count() or 'Runtime-scanned'} local Ollama tags, localhost:11434; free, hardware-bound (i7, 63.7GB RAM, MX350 2GB).",
 "ollama_cloud":"Keyed (OLLAMA_API_KEY) + usage tracker proxy :11435; glm/minimax/qwen-coder.",
 "groq":"Ultra-fast inference; NO key on disk (reachable if keyed).",
 "together":"Broad open-model host; NO key on disk.",
 "fireworks":"Open-model host; NO key on disk.",
 "perplexity":"Search-augmented; NO key on disk.",
 "openrouter":"300+ models single key; NO key on disk.",
 "xai_api":"Grok; NO key on disk.",
 "mistral_api":"NO key on disk.","deepseek_api":"NO key on disk.","cohere_api":"NO key on disk.","qwen_api":"DashScope; NO key on disk.",
 "openai_api":"No standalone OpenAI key (Codex uses ChatGPT plan instead).",
}
order=["claude_cli","claude_desk","anthropic_api","codex_cli","copilot","github_models","gemini_cli","gemini_api","antigravity","bedrock","azure","ollama_local","ollama_cloud","groq","together","fireworks","perplexity","openrouter","xai_api","mistral_api","deepseek_api","cohere_api","qwen_api","openai_api"]
rr=2
for sc in order:
    if sc not in SURF: continue
    lbl,styp,auth,bill,loc=SURF[sc]
    for i,v in enumerate([lbl,styp,auth,bill,loc,snotes.get(sc,"")],1): ws.cell(row=rr,column=i,value=v)
    rr+=1
for row in ws.iter_rows(min_row=2,max_row=rr-1,min_col=1,max_col=len(hdr)):
    for cell in row: cell.font=BASE_FONT; cell.border=BORDER; cell.alignment=Alignment(vertical="center",wrap_text=True)
ws.freeze_panes="A2"
for col,w in zip("ABCDEF",[26,14,30,26,11,60]): ws.column_dimensions[col].width=w

ws=wb.create_sheet("09 Source Catalog & Weights")
ws.cell(row=1,column=1,value="SOURCE CATALOG & CREDIBILITY WEIGHTS - what drives the ratings (verify, do not trust)").font=Font(name="Arial",bold=True,size=12)
sh=["Source Type","Source","URL / Location","Governs","Weight / Trust Rule","Roster Use"]
for i,h in enumerate(sh,1): ws.cell(row=2,column=i,value=h)
style_header(ws,2,len(sh))
rr=3
for row09 in SOURCES:
    for i,v in enumerate(row09,1): ws.cell(row=rr,column=i,value=v)
    rr+=1
for row in ws.iter_rows(min_row=3,max_row=rr-1,min_col=1,max_col=len(sh)):
    for cell in row: cell.font=BASE_FONT; cell.border=BORDER; cell.alignment=Alignment(vertical="center",wrap_text=True)
ws.freeze_panes="A3"
for c,w in zip("ABCDEF",[16,30,40,46,34,40]): ws.column_dimensions[c].width=w
with open(os.path.join(CSVDIR,"09_Source_Catalog.csv"),"w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(sh); [w.writerow(list(row09)) for row09 in SOURCES]

def build_local_bench_results_sheet():
    ws=wb.create_sheet("10 Local Bench Results")
    headers=[
        "result_type","model","surface","benchmark_or_pack","domain_or_task","runs","passed",
        "pass_rate","available","wall_duration_ms","failure_class_or_modes","source_file","notes"
    ]
    smoke_summary_path=os.path.join(OUT,"benchmark_results","local_ollama_benchmark_20260607_163127_summary.json")
    operation_contract_path=os.path.join(OUT,"orchestrator_contracts","local_model_operation_scores_2026-06-07.json")
    rows=[]

    if os.path.exists(smoke_summary_path):
        with open(smoke_summary_path,encoding="utf-8") as f:
            smoke=json.load(f)
        for model,item in sorted(smoke.get("by_model",{}).items()):
            rows.append({
                "result_type":"local_smoke_20s_cold_probe",
                "model":model,
                "surface":"ollama_http",
                "benchmark_or_pack":smoke.get("benchmark_id","BENCH-LOCAL-001"),
                "domain_or_task":"local_runtime_adapter",
                "runs":1,
                "passed":1 if item.get("passed") else 0,
                "pass_rate":1.0 if item.get("passed") else 0.0,
                "available":1 if item.get("available") else 0,
                "wall_duration_ms":item.get("wall_duration_ms"),
                "failure_class_or_modes":item.get("error_class"),
                "source_file":smoke.get("result_path",smoke_summary_path),
                "notes":"20-second cold responsiveness probe; failure is not a final quality score.",
            })

    if os.path.exists(operation_contract_path):
        with open(operation_contract_path,encoding="utf-8") as f:
            operation=json.load(f)
        pack=operation.get("task_pack",{})
        for model,score in sorted(operation.get("model_scores",{}).items()):
            rows.append({
                "result_type":"operation_overall",
                "model":model,
                "surface":"ollama_http",
                "benchmark_or_pack":pack.get("pack_id","operations_first_pack_2026-06-07"),
                "domain_or_task":"all_operation_domains",
                "runs":score.get("runs"),
                "passed":score.get("passed"),
                "pass_rate":score.get("pass_rate"),
                "available":"",
                "wall_duration_ms":score.get("avg_wall_duration_ms"),
                "failure_class_or_modes":json.dumps(score.get("failure_modes",{}),sort_keys=True),
                "source_file":operation_contract_path,
                "notes":"Strict one-trial operation fixture matrix; route only where validators pass.",
            })
            for domain,domain_score in sorted(score.get("domains",{}).items()):
                rows.append({
                    "result_type":"operation_domain",
                    "model":model,
                    "surface":"ollama_http",
                    "benchmark_or_pack":pack.get("pack_id","operations_first_pack_2026-06-07"),
                    "domain_or_task":domain,
                    "runs":domain_score.get("runs"),
                    "passed":domain_score.get("passed"),
                    "pass_rate":domain_score.get("pass_rate"),
                    "available":"",
                    "wall_duration_ms":domain_score.get("avg_wall_duration_ms"),
                    "failure_class_or_modes":json.dumps(domain_score.get("failure_modes",{}),sort_keys=True),
                    "source_file":operation_contract_path,
                    "notes":"Domain score from deterministic fixture validator.",
                })

    if not rows:
        rows.append({
            "result_type":"not_run",
            "model":"",
            "surface":"",
            "benchmark_or_pack":"",
            "domain_or_task":"",
            "runs":0,
            "passed":0,
            "pass_rate":0,
            "available":"",
            "wall_duration_ms":"",
            "failure_class_or_modes":"",
            "source_file":"",
            "notes":"Run local smoke and operation benchmarks before using this sheet for routing.",
        })

    for i,h in enumerate(headers,1):
        ws.cell(row=1,column=i,value=h)
    style_header(ws,1,len(headers))
    for ridx,row in enumerate(rows,2):
        for cidx,h in enumerate(headers,1):
            ws.cell(row=ridx,column=cidx,value=row.get(h,""))
    for row in ws.iter_rows(min_row=2,max_row=len(rows)+1,min_col=1,max_col=len(headers)):
        for cell in row:
            cell.font=BASE_FONT
            cell.border=BORDER
            cell.alignment=Alignment(vertical="center",wrap_text=True)
    ws.freeze_panes="A2"
    ws.auto_filter.ref=ws.dimensions
    for c,wid in zip("ABCDEFGHIJKLM",[26,28,14,34,34,10,10,12,12,18,32,42,70]):
        ws.column_dimensions[c].width=wid
    with open(os.path.join(CSVDIR,"10 Local Bench Results.csv"),"w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

build_local_bench_results_sheet()

ws=wb.create_sheet("00 README & Rating System")
wb.move_sheet("00 README & Rating System",-(len(wb.sheetnames)-1))
ws.column_dimensions["A"].width=34; ws.column_dimensions["B"].width=110
def line(r,a,b="",bold=False,fill=None,size=10):
    ws.cell(row=r,column=1,value=a).font=Font(name="Arial",bold=bold,size=size)
    c=ws.cell(row=r,column=2,value=b); c.font=Font(name="Arial",size=size); c.alignment=Alignment(wrap_text=True,vertical="top")
    if fill: ws.cell(row=r,column=1).fill=fill
rn=1
ws.cell(row=rn,column=1,value="AI MODEL QUALITY ROSTER - Stat-Sheet & Routing System").font=Font(name="Arial",bold=True,size=14); rn+=1
line(rn,"Generated","2026-06-07  |  Machine: Dell Inspiron 7706 (DESKTOP-LOOCRQ2)  |  Author: Matt Couch / Example Consulting  |  CURRENCY SWEEP 2026-06-07",bold=True); rn+=1
line(rn,"Purpose","Per-model capability rating system (video-game character-sheet style) so the AI Orchestrator (AI_ORCHESTRATOR_SPEC_v4.0) routes each job/role to the best-fit model. Row grain = model x surface. Feeds the orchestrator capabilities table."); rn+=2
line(rn,"SCALE",bold=True,fill=GRP_FILL); rn+=1
line(rn,"0 to 10 (one decimal)","Every stat is 0-10. Decimals allowed where a benchmark backs them (breaks ties for routing). Whole numbers where evidence is weaker (no false precision). 10 = best observed in class; 5 = mid."); rn+=1
line(rn,"Group composite (star)","Mean of the sub-stats in that group (Excel AVERAGE formula)."); rn+=1
line(rn,"POWER LEVEL","Weighted overall: Reasoning .19, Coding .17, Agentic .17, Reliability .16, Performance .12, Creative .10, Predictive .09. Multimodal is a separate axis (not in Power Level)."); rn+=2
line(rn,"CONFIDENCE",bold=True,fill=GRP_FILL); rn+=1
line(rn,"H (green)","Benchmark-anchored from 3+ 2026 sources (AA Intelligence Index, SWE-bench Verified/Pro, GPQA Diamond, ARC-AGI-2, HLE, Aider Polyglot, tau-bench/BFCL, MMMU-Pro, MTEB, image arena).",fill=CONF_FILL["H"]); rn+=1
line(rn,"M (amber)","Partly anchored or strong secondary sources; some stats family-derived.",fill=CONF_FILL["M"]); rn+=1
line(rn,"L (red)","Family-inherited estimate (scaled from the flagship of the same family). Verify before HIGH-stakes routing. Refinement path: pull the model's own benchmark card.",fill=CONF_FILL["L"]); rn+=2
line(rn,"NORMALIZATION",bold=True,fill=GRP_FILL); rn+=1
line(rn,"Benchmark to 0-10","A benchmark percent s maps to round(s/10, 1), capped 0-10. Example: SWE-bench Verified 88.6 -> Agentic SWE 8.9. Cost-Efficiency favors local_free and low price; raw price is in the economic columns."); rn+=2
line(rn,"THE 8 LLM STAT GROUPS",bold=True,fill=GRP_FILL); rn+=1
for gname,subs in LLM_GROUPS:
    line(rn,gname,", ".join(subs)); rn+=1
rn+=1
line(rn,"ROUTING USE",bold=True,fill=GRP_FILL); rn+=1
line(rn,"How the orchestrator uses this","Match the job required capability to the top group stat, filter by consequence_max and Auth-on-Dell/Billing (prefer local_free > subscription_included; metered surfaces are not configured on this PC), then rank by the group composite, then Performance for ties. rating_instruction/rating_quality (1-5) drop into the v4.0 capabilities table."); rn+=2
line(rn,"DATA FRESHNESS / KNOWN DRIFT",bold=True,fill=CONF_FILL["M"]); rn+=1
line(rn,"Source-list vs canonical","The uploaded AI_COMPLETE_MODEL_LIST calls gpt-5.4 the flagship; the canonical roster and 2026 web sources show gpt-5.5/5.5-pro shipped 2026-04-24, used here. The uploaded list also still contains retired Grok 3 models (roster: retired 2026-05-15). Re-verify monthly."); rn+=2
line(rn,"SHEETS",bold=True,fill=GRP_FILL); rn+=1
line(rn,"01 LLMs","Text/reasoning/coding/agentic (incl. multimodal chat). Full 8-group stat sheet."); rn+=1
line(rn,"02-07","Embeddings/Rerank, Image, Video, Audio/Speech, Vision-OCR-Document, Moderation - type-specific 0-10 stats."); rn+=1
line(rn,"08 Surfaces & Access","The routing services (adapters) with on-Dell auth/billing status from the 2026-06-06 runtime scan."); rn+=1
line(rn,"09 Source Catalog & Weights","Every source with what it GOVERNS and a trust weight (1-5). Provider specs (5) govern model attributes/IDs/status; benchmarks (3-4) set only relative routing scores, not attributes; runtime scan (5, may drift) governs on-Dell access. Rumored names excluded until provider-documented."); rn+=2
line(rn,"10 Local Bench Results","Local Ollama smoke and operation-fixture results. These are measured routing facts, not provider quality claims."); rn+=2
line(rn,"COVERAGE / REFINEMENT PATH",bold=True,fill=CONF_FILL["L"]); rn+=1
line(rn,"Currency (2026-06-07 sweep, rev.3)","Provider-by-provider sweep 2026-06-07. ADDED released-but-missed: Gemma 4 12B IT QAT local Ollama row, Microsoft MAI family (Thinking-1, Code-1-Flash, Image-2.5/+Flash, Transcribe-1.5, Voice-2; 2026-06-02), and MiniMax M3 (Jun). POLICY: only PROVIDER-DOCUMENTED models become rows. PREVIEW rows are provider-documented, not GA, billing=unavailable, and never default-routed. Rumored or unreleased next-gen names are NOT rows until provider docs confirm them - tracked only via Source Catalog (sheet 09) Tier-4 watchlist."); rn+=1
line(rn,"Deferred / refinement path","Niche current models not yet rated (low routing value here, no on-Dell access): Mercury 2 (Inception, fastest ~790 tok/s), IBM Granite 4.0, StepFun Step 3.7 Flash, OpenBMB MiniCPM5, DeepSeek V3.2. Plus deprecated OpenAI base models, ~30 legacy Mistral snapshots, retired Grok 3. Re-run the generator monthly (or schedule it) to keep current."); rn+=2
line(rn,"SOURCES",bold=True,fill=GRP_FILL); rn+=1
line(rn,"Benchmarks","artificialanalysis.ai (Intelligence Index, GPQA, speed/price); swebench.com; llm-stats.com; epoch.ai (Aider Polyglot); awesomeagents.ai (BFCL/tau-bench, MTEB); benchlm.ai (MMMU-Pro); Image Arena (ArtificialAnalysis). Accessed 2026-06-06/07."); rn+=1
line(rn,"On-Dell access","AI_PROVIDER_MODEL_ROSTER_2026-06-06.md + AI_COMPLETE_MODEL_LIST_2026-06-06.md (runtime-verified)."); rn+=1

wb.save(XLSX)
print("Saved",XLSX)
print("Sheets:",len(wb.sheetnames),wb.sheetnames)
print("LLM data rows:",len(csv_rows)-1,"| unique LLMs:",len(LLMS))


# ===== single-source derived artifacts: dated markdown roster + complete list, archive old =====
def _surfs(codes): return ", ".join(SURF[c][0] for c in codes if c in SURF)
DATE="2026-06-07"
allrows=[("LLM",m["prov"],m["name"],m["mid"],m["status"],_surfs(m["surf"])) for m in LLMS]
for grp,data in [("Embedding",EMBED),("Image",IMAGE),("Video",VIDEO),("Audio",AUDIO),("Vision/OCR",VISION),("Moderation",MOD)]:
    for d in data: allrows.append((grp,d[2],d[1],d[0],d[3],_surfs(d[-1])))
cl=[f"# Complete AI Model Inventory - {DATE}","# Dell Inspiron 7706 (DESKTOP-LOOCRQ2)",
 "# SINGLE-SOURCE: regenerated from AI_MODEL_QUALITY_ROSTER by AI_MODEL_QUALITY_ROSTER_generator.py.",
 "# Supersedes AI_COMPLETE_MODEL_LIST_2026-06-06.md (archived).","",
 f"Total addressable entries: {len(allrows)}","",
 "| # | Type | Provider | Model | ID | Status | Surfaces |","|---|---|---|---|---|---|---|"]
for i,(t,p,n,mid,st,sf) in enumerate(allrows,1):
    cl.append(f"| {i} | {t} | {p} | {n} | `{mid}` | {st} | {sf} |")
open(os.path.join(OUT,"AI_COMPLETE_MODEL_LIST_2026-06-07.md"),"w",encoding="utf-8").write("\n".join(cl))
from collections import OrderedDict
byp=OrderedDict()
for t,p,n,mid,st,sf in allrows: byp.setdefault(p,[]).append((t,n,mid,st,sf))
pr=[f"# AI Provider & Model Roster - CANONICAL (generated) - {DATE}","# Machine: Dell Inspiron 7706 (DESKTOP-LOOCRQ2)",
 "# SINGLE-SOURCE: regenerated from AI_MODEL_QUALITY_ROSTER. Supersedes AI_PROVIDER_MODEL_ROSTER_2026-06-06.md (archived).","",
 "POLICY: provider docs govern model existence/specs/status; benchmarks set only relative routing scores; rumored or unreleased names are excluded from rows until provider-documented. Source credibility weights: workbook sheet '09 Source Catalog & Weights'.",""]
for p,items in byp.items():
    pr.append(f"## {p}  ({len(items)})")
    pr.append("| Type | Model | ID | Status | Surfaces |"); pr.append("|---|---|---|---|---|")
    for t,n,mid,st,sf in items: pr.append(f"| {t} | {n} | `{mid}` | {st} | {sf} |")
    pr.append("")
open(os.path.join(OUT,"AI_PROVIDER_MODEL_ROSTER_2026-06-07.md"),"w",encoding="utf-8").write("\n".join(pr))
import glob, shutil
_arch=os.path.join(OUT,"archive"); os.makedirs(_arch,exist_ok=True)
_moved=[]
for _old in glob.glob(os.path.join(OUT,"*_2026-06-06.md")):
    _b=os.path.basename(_old).replace(".md","_SUPERSEDED_by_2026-06-07.md")
    shutil.move(_old, os.path.join(_arch,_b)); _moved.append(_b)
print(f"Markdown artifacts written: {len(allrows)} entries. Archived 06-06: {_moved or 'none in this dir'}")

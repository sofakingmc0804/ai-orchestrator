# AI Model Quality Roster - Currency Verification SOP

**Principle: do not trust, verify.** Currency is a mechanism, not a mood. The roster stays current when every provider fact is tied to an authority, every row carries a Last Verified date, and the generated artifacts are rebuilt from one source.

**Last full sweep: 2026-06-07.** Run `python roster_freshness_check.py` to see staleness at any time.

---

## Completion Test

The roster is current only when all of these are true:

- `python AI_MODEL_QUALITY_ROSTER_generator.py` rebuilds the workbook, CSVs, provider roster, complete list, and source catalog.
- `python roster_freshness_check.py` reports no stale LLM rows inside the chosen tolerance.
- Official provider docs or model cards govern model IDs, status, context, max output, pricing, modalities, and surface availability.
- Benchmarks govern only relative routing scores, not provider attributes.
- Rumored or unreleased model names are excluded from active routing rows until a provider documents them.
- The workbook includes `09 Source Catalog & Weights`, and any new source has an explicit trust rule.

---

## Step 0 - Freshness Check

```powershell
python roster_freshness_check.py
python roster_freshness_check.py --days 7
```

If the oldest Last Verified date is outside tolerance, run the provider sweep below. Provider-preview rows are allowed, but verify them before high-stakes routing.

---

## Step 1 - Official Provider Sweep

Use official pages first. Cross-checkers can explain movement, but they do not outrank provider docs for model attributes.

| Provider | Primary authority | Cross-check use |
|---|---|---|
| OpenAI | developers.openai.com/api/docs/models | confirm GPT, image, audio, embedding, moderation IDs and pricing |
| Anthropic | platform.claude.com/docs/en/about-claude/models/overview | confirm Claude IDs, context, output limits, pricing, cloud surfaces |
| Google | ai.google.dev/gemini-api/docs/models and model cards | confirm Gemini, Nano Banana, Veo, Lyria, Live, TTS, embeddings |
| Google Gemma | ai.google.dev/gemma/docs/core/model_card_4 | confirm open-weight Gemma parameters, context, modalities, and official benchmark table |
| Ollama local | ollama.com/library/*/tags plus `ollama list` / `ollama show` | confirm local tags, artifact size, quantization, context, capabilities, and installed state |
| xAI | docs.x.ai/developers/models | confirm Grok chat/coding/image/video/voice model families |
| DeepSeek | api-docs.deepseek.com and changelog | confirm V4/V3 alias state and retirement dates |
| Mistral | docs.mistral.ai/models/overview | confirm Mistral, Devstral, Codestral, Voxtral, OCR, moderation |
| Cohere | docs.cohere.com/v2/docs/models | confirm Command, Embed, Rerank, Vision, Transcribe families |
| Qwen / Alibaba | help.aliyun.com/zh/model-studio | confirm Qwen current recommended models and limits |
| Meta | ai.meta.com/blog and llama.com model cards | confirm Llama release facts and open-weight attributes |
| Amazon Bedrock | docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html | confirm Bedrock surface availability and provider model catalog |
| Microsoft MAI | microsoft.ai/models and model cards | confirm MAI model family, surface state, and preview status |
| MiniMax | minimax.io/blog and official docs | confirm MiniMax release facts, context, modality, and availability |

Benchmarks to consult for routing scores: SWE-bench, Artificial Analysis, Epoch AI FrontierMath, LMArena / Arena AI, MTEB, BFCL / tau-bench, MMMU-Pro, and provider-published model cards. Record uncertainty in Confidence.

---

## Step 2 - Diff And Decide

- **Provider-documented current model, not in roster:** add it to the generator and cite the provider source.
- **Provider-documented preview:** add it as `Preview`, keep surfaces exact, and avoid high-stakes default routing unless the source supports it.
- **Rumored or unreleased:** do not add an active row. Put the name in this SOP change log only if it needs future tracking.
- **Deprecated or superseded:** mark `Legacy` or remove from active rows when it has no routing value.
- **Local/auth surface changed:** update `08 Surfaces & Access` only after a live local check.

For Ollama local model additions, the completion test is stricter:

```powershell
ollama pull <exact-provider-tag>
ollama cp <exact-provider-tag> <short-routing-alias>   # only when app recipes use the short alias
ollama show <short-routing-alias>
```

Then run one native local API smoke. For thinking models, pass top-level `think=$false` when the consumer needs plain content:

```powershell
$body = @{ model='<short-routing-alias>'; messages=@(@{role='user'; content='Say READY in plain text.'}); stream=$false; think=$false } | ConvertTo-Json -Depth 8
Invoke-RestMethod -Uri 'http://localhost:11434/api/chat' -Method Post -Body $body -ContentType 'application/json'
```

---

## Step 3 - Regenerate

```powershell
python AI_MODEL_QUALITY_ROSTER_generator.py
python roster_freshness_check.py
```

Then search active root files for known stale strings before closing:

```powershell
$stale = @('gpt-5' + '\.3-codex', 'GPT-5' + '\.6', 'Grok ' + 'V9', 'Gemini ' + '3\.5 Pro', 'HOR' + 'IZON') -join '|'
rg -n $stale -g "*.md" -g "*.py" -g "*.csv" -g "!archive/**"
```

No active-root matches is the expected result unless a provider has actually documented one of those names.

---

## Change Log

### 2026-06-07 - current generated roster

- Rebuilt `AI_MODEL_QUALITY_ROSTER_2026-06-07.xlsx`, all CSV exports, `AI_PROVIDER_MODEL_ROSTER_2026-06-07.md`, and `AI_COMPLETE_MODEL_LIST_2026-06-07.md`.
- Added `09 Source Catalog & Weights` to make authority explicit.
- Updated OpenAI GPT-5.5 / 5.5 Pro / 5.4 Pro / 5.4 mini / 5.4 nano, GPT Image 2, GPT Realtime 2, Realtime Translate, and current OpenAI audio/embedding status.
- Updated Anthropic Claude Opus 4.8 / Sonnet 4.6 / Haiku 4.5 official limits and source notes.
- Updated Gemini 3.1 Pro Preview, Gemini 3.5 Flash, Gemini 3 Flash Preview, Gemini image models, Veo 3.1, Gemini Live/TTS, Lyria, and Gemini Embedding 2.
- Updated DeepSeek V4, Qwen3.7/Qwen3.6, MiniMax M3, Mistral, Cohere, Meta Llama 4, Amazon Bedrock, and Microsoft MAI rows.
- Added Gemma 4 12B local QAT through Ollama: pulled `gemma4:12b-it-qat`, copied local alias `gemma4:12b`, verified `ollama show`, and smoke-tested `/api/chat` with `think=false`.
- Archived the superseded 2026-06-06 markdown rosters under `archive/`.

### YYYY-MM-DD - next refresh

- Run Steps 0-3, then record provider docs checked, models added/changed/removed, and any local access changes.

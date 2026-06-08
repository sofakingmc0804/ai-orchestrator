# LM Studio CLI Server Repair Packet

Terminal state: blocked_after_repair_attempt

Consequence: the orchestrator cannot prove the `lm-studio` adapter without a no-touch local server or CLI.

Mechanism:
- Bundled CLI path checked: `C:\Program Files\LM Studio\resources\app\.webpack\lms.exe`.
- The orchestrator probed `http://127.0.0.1:1234/v1/models`.
- Latest repair detail: lms timed out after 35s
- Agent launch of the visible LM Studio app is forbidden by the no-desktop-takeover rule.

Operator action:
1. Open LM Studio manually.
2. Let it finish first-run setup if prompted.
3. Enable or install the bundled `lms` CLI.
4. In a terminal you control, run: `lms server start --bind 127.0.0.1 --port 1234`.
5. Load one small local model.

Completion test:
- `where.exe lms` returns a path, or the bundled CLI path above exists.
- `lms server status` succeeds.
- `Invoke-RestMethod http://127.0.0.1:1234/v1/models` returns at least one model.
- `python -m orchestrator.cli.main dispatch "LM Studio proof"` can produce an `lm-studio` receipt when routed to that adapter.

Forbidden substitutes:
- Do not launch the visible LM Studio app from an agent.
- Do not use direct OpenAI, Anthropic, OpenRouter, or direct Gemini API as a fallback.
- Do not mark CT-12 complete until `lm-studio` has a receipt or the adapter is explicitly removed from discovered services.

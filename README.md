# Mira low-latency voice agent

Mira is a customer-success voice agent whose primary demo is an explicit browser VAD → OpenAI STT → OpenAI LLM → OpenAI TTS pipeline backed by FastAPI. The OpenAI Realtime/WebRTC implementation remains available as a Realtime comparison mode. Both paths include per-turn latency instrumentation and two controlled post-tool response modes for the assignment benchmark.

## Response modes

Set exactly one mode in `backend/.env`:

```env
# Baseline: the model composes a reply from the tool result.
RESPONSE_MODE=llm

# Optimized: the backend supplies policy-approved response text directly to TTS.
RESPONSE_MODE=speech_directive
```

The flag changes only post-tool response composition. STT, VAD, models, voice, tools, prompts, and measurement code stay the same so the comparison isolates the optimization.

## Conversation context and safe state

The primary pipeline keeps a short rolling conversational history for the active call and does not perform a separate summarization request. Realtime mode lets the Realtime session own its model context. In both paths, backend workflow state remains separate from the model transcript and is deleted when the call disconnects.

The prompt is deliberately limited to five stable instruction sections: `identity`, `behavior`, `tools`, `principles`, and `guardrails`. Mutable facts do not go into the prompt. Identity, pending purpose, and per-field request attempts remain explicit backend `SessionState` fields, while current record values come from tools. This server-owned state prevents the model from resetting `attempt` and asking for the same information again.

Realtime truncation uses a `0.8` retention ratio. This avoids an in-call summarization request and reduces repeated cache busting if a long session eventually reaches its context limit. Important workflow state remains safe because it is represented in backend state instead of relying only on old transcript turns.

## Grouped tool contract

The realtime model sees exactly three tools:

- `customer_identity`
- `customer_lookup`
- `support_callback`

Every tool has the same strict input contract: `purpose`, nullable `caller_name`, nullable `caller_phone`, nullable `order_id`, nullable `ticket_id`, and integer `attempt`. The only purposes are `order_status`, `ticket_status`, and `customer_support`. The active purpose is reused through follow-ups, identity, and lookup. It changes only when the caller clearly starts a different supported request.

`action` is a backend result, not a model-selected input. It tells the client what deterministic next step to take. Transition actions such as `ready_for_lookup` and `route_to_callback` continue locally without another LLM decision. The response returns the current purpose, action, normalized caller fields, safe information, and selected directive:

```json
{
  "purpose": "ticket_status",
  "action": "return_ticket_status",
  "caller_name": "John Carper",
  "caller_phone": null,
  "order_id": null,
  "ticket_id": "4821",
  "attempt": 0,
  "information": {
    "case_id": "4821",
    "status": "Refund pending",
    "priority": "High"
  },
  "directive": {
    "key": "customer_lookup.ticket_status.success",
    "response_text": "Your ticket 4821 is Refund pending. Its priority is High.",
    "require_repeat_verbatim": true
  }
}
```

Directives live in one flat string registry keyed by state, for example `customer_identity.order_status.no_caller_name`, `customer_lookup.order_status.no_order_id`, and `customer_lookup.ticket_status.no_ticket_id`. A unique normalized exact full name is enough to identify one mock customer; phone digits are required for multiple or fuzzy matches. The relevant order or ticket ID is required for lookup. If a requested field is declined or remains absent, the server returns `route_to_callback` and will not request that field again during the call.

Customer-name candidate discovery normalizes case, accents, punctuation, and whitespace, then uses RapidFuzz weighted-ratio scoring. A unique normalized exact full-name match can continue directly. Partial names, reordered names, and typo-tolerant fuzzy matches scoring at least 80 are candidates only and always require phone verification; scores of at least 92 are labeled strong fuzzy matches for observability but still do not bypass verification.

## Run the demo

Copy `backend/.env.example` to `backend/.env`, add `OPENAI_API_KEY`, and then run:

```powershell
py -m pip install -r backend/requirements.txt
py -m uvicorn backend.main:app --reload --port 8000
```

In a second terminal:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open `http://localhost:5173`, click the microphone button, and speak. The page shows the current response mode, turn-by-turn breakdown, completed-turn count, and median/p95 latency.

## Measurements

Each completed spoken turn records:

| Metric | Primary pipeline measurement |
| --- | --- |
| End-of-user-speech detection | Last locally voiced frame to the browser's local VAD endpoint |
| STT | Backend transcription request start to completed transcript |
| LLM TTFT | Model request start to first streamed token or tool-call delta |
| LLM total | Model request start to complete model/tool decision |
| TTS time-to-first-audio | TTS request start to first received audio bytes |
| End-to-heard audio | Last locally voiced frame to the browser's first playback event |

OpenAI Realtime mode is fused speech-to-speech, so its STT/LLM/TTS values are event-boundary proxies rather than isolated provider request timings. End-to-heard remains the primary user-perceived metric on both paths.

Samples are appended to `backend/data/latency_samples.jsonl` and reloaded after backend restarts. Raw data is available from:

- `GET /latency/samples`
- `GET /latency/export.csv`
- the **Export CSV** button in the demo

Summary statistics are available from `GET /latency/summary`. It reports both all-turn metrics and `tool_metrics`, so the optimization can be evaluated without unrelated conversational turns diluting the result. p95 uses the nearest-rank definition.

## Benchmark procedure

1. Set `RESPONSE_MODE=llm` and restart the backend.
2. Run at least 20 comparable tool interactions using the same script, microphone, room, network, and tool mix.
3. Set `RESPONSE_MODE=speech_directive`, restart, and repeat the same interactions.
4. Export the CSV and report median and p95 for both modes.
5. Compare tool-call turns when evaluating the directive optimization; non-tool turns intentionally follow the same path in both modes.

Suggested repeatable tool flows: ask for order `1234` and identify as John Carper for the unique-name path; ask for ticket `4821`, identify as John, and use `1198` for the multiple-name path; or decline a requested name or record ID to exercise immediate callback escalation. Start a fresh call when resetting identity state is useful.

Do not fabricate benchmark results. The repository supplies the collector and analysis; final before/after numbers must come from real microphone interactions in the target environment.

## Tests

```powershell
py -m unittest discover -s tests -v
cd frontend
npm.cmd run build
```

## Architecture

1. The browser runs local audio-energy VAD, keeps a short pre-roll, and detects the end of the caller's turn.
2. The completed WAV turn is sent to FastAPI and transcribed with a narrow English/identifier-aware STT prompt.
3. The text model either responds conversationally or selects one of the three backend tools.
4. FastAPI executes the tool state machine against local mock data and retains per-field attempt state for only the active call.
5. In `llm` mode, the tool result returns to the model for response composition.
6. Transition actions are chained locally, so lookup or callback does not require another model decision.
7. In `speech_directive` mode, FastAPI sends the selected directive text straight to TTS.
8. The browser starts playback, records the first heard-audio milestone, and shows the turn beside the live latency breakdown.
9. Disconnect closes capture/playback resources and idempotently deletes backend session state and trace history. The Realtime tab retains the WebRTC implementation for comparison.

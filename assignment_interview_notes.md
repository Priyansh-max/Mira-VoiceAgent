# Voice Agent Assignment — Interview Notes

> Status: pre-benchmark working notes. Replace the hypothesis language below with measured median and p95 values only after both 20-turn runs are complete. Do not claim a largest measured bottleneck before the CSV confirms it.

## Largest bottleneck

The current hypothesis is that the largest avoidable latency is pipeline serialization rather than the local tool itself. The baseline waits for a complete recording, a complete STT result, a complete LLM/tool-planning stream, a complete TTS response, and then a complete base64 JSON download before browser playback begins. Although the LLM and TTS SDK calls expose streams, those streams are currently consumed completely inside the backend and do not reduce the user's wait.

End-of-turn detection is also a known fixed contributor. The chained browser pipeline currently waits for 650 ms of silence. The measurement run must determine whether endpointing, STT, model generation, full TTS buffering, or browser/transport delivery is largest in the actual environment.

On tool-backed turns, the second model pass remains a separate hypothesis. Speech-directive mode removes that composition pass by returning approved response text from the local tool backend. General conversation still uses the LLM in both modes.

For the final report, compare `end_of_speech_detection_ms`, `stt_ms`, `llm_ttft_ms`, `llm_total_ms`, `tool_round_trip_ms`, `tts_ttf_audio_ms`, `tts_total_ms`, `delivery_overhead_ms`, and `total_to_first_audio_ms`. Report general and tool turns separately.

## Why optimize this part

The optimization targets post-tool response generation because tool results already determine the safe answer. Asking the LLM to restate a deterministic order status, ticket status, missing-field question, or callback confirmation adds another inference round without adding useful reasoning.

The first optimization experiment is true cross-stage streaming because it attacks waits shared by both general and tool-backed turns. Speech-directive mode remains the narrower tool-turn optimization. Together they answer two different questions:

- streaming tests whether removing full-stage buffering improves every spoken turn;
- directives test whether removing the second model pass improves deterministic tool turns;
- both preserve the same local tools and mock data, isolating provider and orchestration latency;
- the four-condition comparison separates the benefit of streaming from the benefit of deterministic tool speech.

The custom pipeline must not be described as running “over OpenAI Realtime WebRTC.” OpenAI Realtime is a separate speech-to-speech architecture. A stage-separated implementation needs its own persistent browser/backend transport (WebSocket or a WebRTC media layer such as LiveKit) and streaming connections to STT, LLM, and TTS.

## Worse network conditions

Local tools remove database-network variance, but the call is not network-free. WebRTC still crosses the network to OpenAI, and the browser calls the local backend for tool execution and metric persistence.

Under higher RTT, jitter, or packet loss:

- session setup and tool-result delivery would take longer;
- model events and audio packets could arrive later or unevenly;
- time to first audio and p95 latency would increase more visibly than median latency;
- packet loss concealment may preserve intelligibility while making audio feel less immediate;
- reconnects or ICE failures may become visible failure modes;
- the output-energy measurement may lag the first server audio event by a larger and less stable amount.

The final report should record network type and test both modes under the same conditions. A throttled-network run would be a useful secondary experiment, but it must not be mixed into the primary 20-turn dataset.

## How to reduce latency further

1. Stream microphone frames to STT while the caller is speaking instead of uploading a completed WAV.
2. Send final STT text directly into a streaming LLM request.
3. Chunk stable LLM phrases into TTS and stream playable PCM/audio frames to the browser immediately.
4. Measure and cautiously reduce the current 650 ms silence duration, validating that shorter values do not cut callers off.
5. Compare fixed VAD with semantic or STT-aware turn detection for callers who pause mid-sentence.
6. Shorten directive text and keep only speech-ready fields in tool responses.
7. Deploy the orchestrator close to the providers and reuse warm provider connections.
8. Evaluate smaller/faster models using the same conversation script and correctness checks.
9. Test whether a brief acknowledgement can mask unavoidable latency without delaying the actual tool call.

## Trade-offs introduced

- Speech directives are faster and predictable, but less flexible and expressive than model-composed replies.
- Directive text becomes product content that must be maintained, reviewed, and localized.
- Deterministic client-side tool chaining lowers latency but couples the frontend to backend action names.
- Lower VAD silence thresholds can reduce latency while increasing premature turn endings.
- Streaming and sentence chunking lower time-to-first-audio but can commit speech before the full answer is known, making corrections and cancellation harder.
- Streaming STT may revise partial transcripts; speculative LLM/TTS work can be wasted when the final transcript changes.
- A custom persistent media transport gives provider control but recreates orchestration, backpressure, cancellation, and reconnect logic supplied by LiveKit or Vapi.
- Fuzzy name matching improves tolerance to transcription errors, but it can increase false-positive candidates. The implementation therefore requires phone verification for every fuzzy or ambiguous match.
- Exact-name-only verification is acceptable for the local assignment demo but is not strong production authentication.
- Local mock data makes the optimization easier to isolate, but the measurements do not represent production database or external API latency.
- The directive and LLM modes must use the same model, VAD, WebRTC path, tools, prompts, and test script for a defensible comparison.

## Final evidence checklist

- Four controlled conditions: batch LLM, batch directive, streaming LLM, and streaming directive.
- At least 20 comparable interactions per condition (80 total), using the same ordered script. If time forces a smaller run, do not pool unlike conditions to manufacture a p95.
- Median and nearest-rank p95 for end-to-heard latency.
- Per-stage median and p95 breakdown.
- Tool-call turns reported separately from general-conversation turns.
- Same microphone, room, browser, network, model, voice, VAD, and scenario order.
- Raw CSV attached or reproducible from the app.
- No claim based only on a single turn or on development intuition.

## Controlled 20-turn script

Run the following as four five-turn sessions. Start a new session between groups, and repeat the same wording and order for every benchmark condition.

1. “Hello, I need the status of an order.”
2. “My name is John Carper.”
3. “The order number is 1234.”
4. “What does shipped today mean?”
5. “Thanks, goodbye.”
6. “I need the status of a support ticket.”
7. “My name is John Doe.”
8. “The ticket number is 4822.”
9. “What does under review mean?”
10. “Thank you for the help.”
11. “Can you check my order status?”
12. “My name is John Carver.”
13. “The last four digits are 1198.”
14. “My order number is 1234.”
15. “Is there anything else I need to do?”
16. “I want to check a ticket.”
17. “I do not want to provide my name.”
18. “Actually, I need help with an order instead.”
19. “My name is Priya Sharma.”
20. “I do not have the order number and I will not provide it.”

Record false cutoffs, wrong transcripts, wrong tool choices, and failed audio playback separately from valid latency samples. Do not silently discard failed turns.

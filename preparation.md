# Interview Preparation

## What This Build Is
This is a customer-support voice agent with:

- realtime voice conversation in the browser
- backend tool orchestration
- conversation memory and verification state
- live trace visualization of internal reasoning and tool activity

The final architecture is **browser + OpenAI Realtime over WebRTC** for low-latency speech, with **FastAPI** handling app session state, policy enforcement, tool execution, and trace streaming.

## How We Reached The Final Build
We intentionally built this in stages:

1. We started from a text-first backend because it was cheaper and easier to debug than realtime voice.
2. We built the backend core first: FastAPI routes, in-memory session store, mock tools, and SSE trace streaming.
3. We first used a text orchestration path to validate intent detection, memory, verification, and tool behavior.
4. We refactored the text agent into three layers:
   - Planner
   - Policy Executor
   - Response Composer
5. We made the support flow more realistic by adding:
   - customer identity resolution
   - phone last-4 verification
   - ownership checks for tickets/orders
   - callback escalation
6. After the backend logic became stable, we moved the voice path to OpenAI Realtime over WebRTC for lower latency.
7. We then connected the realtime model to backend tools by letting the model choose tools with `tool_choice=auto`, forwarding function calls to FastAPI, and returning tool results back into the realtime session.
8. Finally, we redesigned the frontend into a demo-ready realtime console with integrated trace.

## Final Capabilities
- Natural voice conversation through the browser.
- Direct realtime audio with OpenAI.
- Text fallback / legacy debug route.
- Tool usage only when needed.
- Protected ticket and order lookup.
- Customer identification by name.
- Customer verification by phone last 4 digits.
- Callback scheduling.
- Live trace of:
  - user transcript
  - assistant transcript
  - intent
  - sentiment
  - tool call
  - tool result
  - policy outcome

## Models We Used
### Primary models
- `gpt-realtime-mini`
  - Used for realtime voice interaction.
  - Handles live speech understanding, response generation, and tool selection.

- `gpt-4.1-mini`
  - Used for the backend text agent.
  - Planner + response composition for the text-first orchestration path.

### Realtime input transcription
- `gpt-4o-mini-transcribe`
  - Used inside the realtime session config for speech transcription.

### Legacy optional endpoints
- `whisper-1`
  - Used in the old `/stt` endpoint.

- `tts-1` with voice `shimmer`
  - Used in the old `/tts` endpoint.

### Realtime output voice
- Voice configured as `marin` in the realtime session config.

## Why We Chose This Architecture
The key design choice was:

**Use OpenAI Realtime directly from the browser for audio, but keep business logic and tools on our backend.**

Why:
- Lowest latency comes from avoiding browser -> backend -> model audio relays.
- Tool calls still need policy enforcement and protected business logic.
- Trace and session state belong on the backend, not in the browser.
- This gives a clean separation:
  - OpenAI handles speech and conversational turn-taking
  - FastAPI handles trust, policy, tools, and observability

## How OpenAI Realtime Works Internally In This Build
At a high level:

1. The frontend asks FastAPI for a short-lived realtime client secret.
2. FastAPI creates an app session and requests a temporary OpenAI realtime client secret.
3. The browser creates a WebRTC connection directly to OpenAI.
4. Microphone audio is streamed directly from the browser to the realtime model.
5. The model transcribes speech, decides whether it can answer directly or should call a tool.
6. If no tool is needed, it responds directly in audio.
7. If a tool is needed, it emits a function call event over the data channel.
8. The frontend forwards that function call to our backend `/realtime/tool`.
9. FastAPI executes the tool through our policy-aware agent layer.
10. The frontend sends the tool result back into the realtime conversation as `function_call_output`.
11. The model then continues the conversation naturally using the tool result.

Important point:
- The realtime model does not directly access our database/tools.
- It only knows tool schemas.
- Our backend remains the decision-enforcement layer for anything sensitive.

## How WebRTC Works Internally In This Build
We use WebRTC because it is better suited than ordinary HTTP for low-latency duplex audio.

Internally:

1. The browser creates an `RTCPeerConnection`.
2. It captures microphone audio using `getUserMedia`.
3. Audio tracks are added to the peer connection with `addTrack`.
4. The browser creates an SDP offer.
5. That SDP offer is posted to OpenAI’s realtime endpoint using the short-lived client secret.
6. OpenAI returns an SDP answer.
7. The browser sets the remote description, which finalizes the audio connection.
8. Audio from the assistant comes back as a remote media track and is played in the browser.
9. A WebRTC data channel is also created:
   - tool call events
   - transcripts
   - status events
   - other realtime events
   all flow through this channel.

So:
- media track = actual audio
- data channel = structured events and function calling

## Important Choices We Made
- Chose **text-first before voice-first** to reduce cost and debugging complexity.
- Chose **OpenAI Realtime over WebRTC** instead of backend-relayed streaming for lower latency.
- Chose **backend-owned tools and policy** instead of trusting the model with sensitive access.
- Chose **trace-first observability** so every internal step is visible during demos/debugging.
- Chose **identity verification before protected lookups** to make the demo more realistic.
- Chose **tool_choice=auto** so the model can answer directly when no tool is needed.
- Kept a **legacy implementation route** so earlier text-debug behavior remains available.

## First-Principles Practices We Followed
- Optimize for latency by removing unnecessary network hops.
- Separate concerns:
  - speech interface
  - reasoning
  - policy
  - tools
  - trace
- Never trust the model alone for protected data access.
- Make state explicit in the backend session instead of relying only on prompt history.
- Make the system observable with fine-grained trace events.
- Prefer deterministic policy code over prompt-only control for security-critical flows.
- Build the cheapest debuggable version first, then add realtime.

## What You Should Be Able To Explain Clearly
- Why we built text-first before realtime.
- Why WebRTC was used instead of standard REST for live audio.
- Why tools stay on FastAPI even though the model can call functions.
- Why verification and ownership checks are handled in Python policy code.
- Why trace is valuable in both demos and debugging.
- Why `tool_choice=auto` is important: the model should not force tool use for every turn.

## Key Code Components To Study Thoroughly
Before the interview, understand these very well:

### Backend
- `backend/main.py`
  - Main FastAPI entrypoint.
  - Routes for sessions, chat, realtime session bootstrap, realtime tool execution, trace SSE.

- `backend/text_agent.py`
  - The most important backend file.
  - Planner, policy executor, response composer, tool execution, verification logic, realtime tool bridge.

- `backend/realtime.py`
  - Realtime session configuration.
  - Tool schema registration.
  - Short-lived client secret creation.

- `backend/conversation.py`
  - Session state model.
  - Memory fields like claimed name, verification status, pending intent, ids.

- `backend/tools.py`
  - Mock backend services.
  - Customer identification, verification, ticket lookup, order lookup, callback scheduling.

- `backend/trace.py`
  - Trace event model and in-memory event bus.
  - SSE history + live subscriptions.

### Frontend
- `frontend/src/components/RealtimeVoicePanel.jsx`
  - WebRTC setup.
  - Data channel event handling.
  - Realtime function call forwarding.
  - Tool result injection back into the realtime model.

- `frontend/src/components/TracePanel.jsx`
  - Trace rendering from backend SSE and client-side realtime events.

- `frontend/src/App.jsx`
  - Main route vs legacy route.
  - Session reset behavior.

- `frontend/src/api.js`
  - Browser-to-backend API layer.

## 30-Second Summary
I built a customer-support voice agent by first validating the business logic in a text-first FastAPI pipeline, then moving the audio path to OpenAI Realtime over WebRTC for lower latency. The browser streams audio directly to OpenAI, but tool execution, verification, policy enforcement, session state, and tracing remain on the FastAPI backend. The result is a low-latency voice interface with safe backend tool use and full internal trace visibility.

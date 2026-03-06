# Voice Agent (Tool Orchestration + Trace)

Voice-enabled customer-support agent: intent/sentiment detection, tool calls, and real-time trace in the UI.

## Backend (FastAPI)

```bash
cd d:\voice-agent
py -m pip install -r requirements.txt
py -m uvicorn backend.main:app --reload --port 8000
```

- **Optional:** Set `OPENAI_API_KEY` for speech-to-text (Whisper) and text-to-speech. If unset, use the text input and browser TTS fallback.

## Frontend (Vite + React)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The app proxies `/api` to the backend.

## Flow

1. Start a session (auto on load or "New session").
2. Use **Hold to talk** (mic) or type a message.
3. With `OPENAI_API_KEY`: mic → backend STT → agent → response → backend TTS (or browser TTS).
4. Trace panel shows intent, sentiment, tool calls, and response in real time.

## API (backend)

- `POST /sessions` — create session, returns `session_id`
- `POST /chat` — `{ "session_id", "text" }` → `{ "response_text" }`
- `GET /trace/{session_id}` — SSE stream of trace events
- `POST /stt` — upload audio file → `{ "text" }` (needs OpenAI)
- `POST /tts` — `{ "text" }` → audio/mpeg (needs OpenAI)
- `GET /stt/available`, `GET /tts/available` — feature flags

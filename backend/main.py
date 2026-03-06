from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from backend.conversation import ConversationStore
from backend.realtime import RealtimeConfigError, create_realtime_client_secret
from backend.text_agent import OpenAITextAgent, TextAgentConfigError
from backend import stt
from backend import tts
from backend.trace import TraceEvent, TraceStore


load_dotenv(Path(__file__).with_name(".env"))

app = FastAPI(title="Voice Agent Backend", version="0.1.0")

# Frontend will run on a different port later (Vite), so enable CORS now.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

conversations = ConversationStore()
traces = TraceStore()
text_agent = OpenAITextAgent()


class CreateSessionResponse(BaseModel):
    session_id: str


class ChatRequest(BaseModel):
    session_id: str
    text: str = Field(min_length=1)


class ChatResponse(BaseModel):
    session_id: str
    response_text: str


class TtsRequest(BaseModel):
    text: str = Field(min_length=1)


class RealtimeSessionResponse(BaseModel):
    app_session_id: str
    client_secret: str
    expires_at: int
    realtime_session: dict


class RealtimeToolRequest(BaseModel):
    session_id: str
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)


class RealtimeToolResponse(BaseModel):
    tool_name: str
    tool_result: Optional[dict] = None
    policy_outcome: dict
    session_state: dict


@app.get("/health")
def health() -> dict:
    return {"ok": True}


# CURRENT APPROACH:
# The frontend will ask for a short-lived Realtime client secret, then connect
# directly to OpenAI over WebRTC for low-latency voice input/output.
#
# OLD APPROACH:
# The backend directly handled /stt -> /chat -> /tts in separate REST calls.
# We are keeping that older path below during the migration.
@app.post("/realtime/session", response_model=RealtimeSessionResponse)
def create_realtime_session() -> RealtimeSessionResponse:
    app_session = conversations.create_session()

    try:
        realtime = create_realtime_client_secret()
    except RealtimeConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    traces.emit(
        TraceEvent(
            session_id=app_session.session_id,
            type="session",
            message="App session created for realtime conversation",
            data={"mode": "realtime_webrtc"},
        )
    )
    traces.emit(
        TraceEvent(
            session_id=app_session.session_id,
            type="realtime_session",
            message="Issued short-lived OpenAI Realtime client secret",
            data={
                "expires_at": realtime["expires_at"],
                "model": realtime.get("session", {}).get("model"),
                "voice": realtime.get("session", {})
                .get("audio", {})
                .get("output", {})
                .get("voice"),
            },
        )
    )

    return RealtimeSessionResponse(
        app_session_id=app_session.session_id,
        client_secret=realtime["client_secret"]["value"],
        expires_at=realtime["expires_at"],
        realtime_session=realtime["session"],
    )


@app.post("/realtime/tool", response_model=RealtimeToolResponse)
def execute_realtime_tool(req: RealtimeToolRequest) -> RealtimeToolResponse:
    try:
        session = conversations.get_session(req.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id")

    try:
        result = text_agent.execute_realtime_tool(
            session=session,
            trace=traces,
            tool_name=req.tool_name,
            tool_args=req.tool_args,
        )
    except TextAgentConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return RealtimeToolResponse(**result)


# LEGACY ENDPOINT:
# This route is still useful for testing the old text-only orchestration path,
# but it is no longer the main direction for the natural voice experience.
@app.post("/sessions", response_model=CreateSessionResponse)
def create_session() -> CreateSessionResponse:
    session = conversations.create_session()
    traces.emit(
        TraceEvent(
            session_id=session.session_id,
            type="session",
            message="Session created",
        )
    )
    return CreateSessionResponse(session_id=session.session_id)


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    # OLD:
    # `backend/agent.py` handled this with rule-based regex/keyword logic.
    #
    # CURRENT:
    # `/chat` now uses the cheaper OpenAI text-only path so we can validate
    # tools, traces, and session behavior before paying for realtime voice.
    #
    # LATER:
    # the frontend voice flow will move to `/realtime/session` + WebRTC, while
    # reusing the same backend ideas around tools, session state, and tracing.
    try:
        session = conversations.get_session(req.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id")

    try:
        response_text = text_agent.handle_text(session=session, text=req.text, trace=traces)
    except TextAgentConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return ChatResponse(session_id=session.session_id, response_text=response_text)


def _sse_pack(event: TraceEvent) -> str:
    payload = json.dumps(event.model_dump(), ensure_ascii=False)
    return f"data: {payload}\n\n"


async def _trace_stream(session_id: str) -> AsyncIterator[str]:
    q = traces.subscribe(session_id)

    # Send history first (so the UI can show context after refresh)
    for ev in traces.history(session_id):
        yield _sse_pack(ev)

    last_send = time.time()
    try:
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15)
                yield _sse_pack(ev)
                last_send = time.time()
            except asyncio.TimeoutError:
                # keepalive comment
                if time.time() - last_send >= 15:
                    yield ": keepalive\n\n"
                    last_send = time.time()
    finally:
        traces.unsubscribe(session_id, q)


@app.get("/trace/{session_id}")
async def trace_sse(session_id: str) -> StreamingResponse:
    # If session doesn't exist, fail fast (nice DX)
    try:
        _ = conversations.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id")

    return StreamingResponse(_trace_stream(session_id), media_type="text/event-stream")


# LEGACY ENDPOINTS:
# These remain temporarily so the old demo path keeps working while we move
# toward browser WebRTC + OpenAI Realtime for both input and output audio.
#
# ----- STT (speech-to-text) -----


@app.post("/stt")
async def speech_to_text(audio: UploadFile = File(...)) -> dict:
    """Upload audio (e.g. webm); returns { \"text\": \"...\" } or { \"text\": null, \"error\": \"...\" }."""
    if not stt.is_available():
        return {"text": None, "error": "STT not configured. Set OPENAI_API_KEY for Whisper."}
    data = await audio.read()
    if not data:
        return {"text": None, "error": "Empty audio"}
    content_type = audio.content_type or "audio/webm"
    text = stt.transcribe(data, content_type=content_type)
    return {"text": text}


@app.get("/stt/available")
def stt_available() -> dict:
    return {"available": stt.is_available()}


# ----- TTS (text-to-speech) -----


@app.post("/tts")
async def text_to_speech(req: TtsRequest):
    """Returns MP3 bytes if OpenAI TTS is configured, else 503."""
    if not tts.is_available():
        raise HTTPException(
            status_code=503,
            detail="TTS not configured. Set OPENAI_API_KEY or use browser TTS.",
        )
    audio_bytes = tts.synthesize(req.text)
    if not audio_bytes:
        raise HTTPException(status_code=500, detail="TTS synthesis failed")
    from fastapi.responses import Response
    return Response(content=audio_bytes, media_type="audio/mpeg")


@app.get("/tts/available")
def tts_available() -> dict:
    return {"available": tts.is_available()}
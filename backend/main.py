from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from backend.conversation import ConversationStore, expected_information_field
from backend.realtime import RealtimeConfigError, create_realtime_client_secret
from backend.realtime_tools import RealtimeToolError, RealtimeToolRouter
from backend.text_agent import OpenAITextAgent, TextAgentConfigError
from backend.pipeline_agent import ChainedPipelineAgent, PipelineAgentConfigError
from backend import stt
from backend import tts
from backend.trace import TraceEvent, TraceStore
from backend.config import get_response_mode
from backend.latency import LatencySample, LatencyStore


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
pipeline_agent = ChainedPipelineAgent()
realtime_tools = RealtimeToolRouter()
latency_log_value = os.environ.get("LATENCY_LOG_PATH", "").strip()
latency_log_path = Path(latency_log_value) if latency_log_value else Path(__file__).with_name("data") / "latency_samples.jsonl"
latency = LatencyStore(log_path=latency_log_path)


class CreateSessionResponse(BaseModel):
    session_id: str


class DeleteSessionResponse(BaseModel):
    session_id: str
    deleted: bool


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
    response_mode: str


class RealtimeToolRequest(BaseModel):
    session_id: str
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)


class RealtimeToolResponse(BaseModel):
    purpose: str
    action: str
    caller_name: str | None
    caller_phone: str | None
    order_id: str | None
    ticket_id: str | None
    callback_time: str | None
    attempt: int
    information: dict
    session_state: dict
    response_mode: str
    directive: dict | None = None
    backend_tool_ms: float


class PipelineSessionResponse(BaseModel):
    session_id: str
    response_mode: str
    pipeline_mode: str


class PipelineTurnResponse(BaseModel):
    session_id: str
    turn_id: str
    transcript: str
    response_text: str
    response_mode: str
    has_tool_call: bool
    audio_base64: str
    audio_media_type: str
    tool_result: dict | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    metrics: dict[str, float | None]


@app.get("/health")
def health() -> dict:
    try:
        response_mode = get_response_mode()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True, "response_mode": response_mode}


@app.delete("/session/{session_id}", response_model=DeleteSessionResponse)
def delete_session(session_id: str) -> DeleteSessionResponse:
    """Idempotently discard all backend state owned by a completed call."""
    session_deleted = conversations.delete_session(session_id)
    trace_deleted = traces.clear_session(session_id)
    return DeleteSessionResponse(
        session_id=session_id,
        deleted=session_deleted or trace_deleted,
    )


@app.post("/pipeline/session", response_model=PipelineSessionResponse)
def create_pipeline_session() -> PipelineSessionResponse:
    try:
        response_mode = get_response_mode()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    session = conversations.create_session()
    traces.activate_session(session.session_id)
    traces.emit(
        TraceEvent(
            session_id=session.session_id,
            type="session",
            message="App session created for chained voice pipeline",
            data={"mode": "stt_llm_tts", "response_mode": response_mode},
        )
    )
    return PipelineSessionResponse(
        session_id=session.session_id,
        response_mode=response_mode,
        pipeline_mode="stt_llm_tts",
    )


@app.post("/pipeline/turn", response_model=PipelineTurnResponse)
async def run_pipeline_turn(
    session_id: str = Form(...),
    turn_id: str = Form(...),
    audio: UploadFile = File(...),
) -> PipelineTurnResponse:
    backend_started_at = time.perf_counter()
    try:
        session = conversations.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio")

    content_type = audio.content_type or "audio/webm"
    expected_field = expected_information_field(session)
    try:
        transcript, stt_ms = stt.transcribe_with_timing(
            audio_bytes,
            content_type=content_type,
            expected_field=expected_field,
        )
    except stt.SpeechToTextError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audio_duration_seconds = stt.wav_duration_seconds(audio_bytes, content_type=content_type)
    if transcript and stt.transcript_is_implausible(
        transcript,
        audio_duration_seconds=audio_duration_seconds,
    ):
        raise HTTPException(
            status_code=422,
            detail="STT result was rejected because it could not plausibly fit in the captured audio.",
        )
    if not transcript and not expected_field:
        raise HTTPException(
            status_code=422,
            detail=(
                "STT received the audio but found no speech "
                f"({len(audio_bytes)} bytes, {content_type})."
            ),
        )

    if transcript:
        traces.emit(
            TraceEvent(
                session_id=session.session_id,
                type="user_transcript",
                message="User transcript received",
                data={"text": transcript},
            )
        )
    else:
        traces.emit(
            TraceEvent(
                session_id=session.session_id,
                type="recognition_repair",
                message="Expected information was not recognized",
                data={"expected_field": expected_field},
            )
        )

    try:
        if transcript:
            agent_result = pipeline_agent.handle_text(session=session, text=transcript)
        else:
            agent_result = pipeline_agent.handle_unrecognized_capture(
                session=session,
                expected_field=expected_field,
            )
    except (PipelineAgentConfigError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    response_text = agent_result["response_text"]
    audio_out, tts_ttf_audio_ms, tts_total_ms = tts.synthesize_with_timing(response_text)
    if not audio_out:
        raise HTTPException(status_code=503, detail="TTS synthesis failed")

    traces.emit(
        TraceEvent(
            session_id=session.session_id,
            type="assistant_transcript",
            message="Assistant response generated",
            data={
                "text": response_text,
                "response_mode": agent_result["response_mode"],
                "has_tool_call": agent_result["has_tool_call"],
                "tool_result": agent_result["tool_result"],
                "tool_calls": agent_result["tool_calls"],
            },
        )
    )

    encoded_audio = base64.b64encode(audio_out).decode("ascii")
    backend_processing_ms = round((time.perf_counter() - backend_started_at) * 1000, 1)
    stage_total_ms = sum(
        value or 0
        for value in (
            stt_ms,
            agent_result["llm_total_ms"],
            agent_result["tool_round_trip_ms"],
            tts_total_ms,
        )
    )
    metrics = {
        "stt_ms": stt_ms,
        "llm_ttft_ms": agent_result["llm_ttft_ms"],
        "llm_total_ms": agent_result["llm_total_ms"],
        "tool_round_trip_ms": agent_result["tool_round_trip_ms"],
        "tts_ttf_audio_ms": tts_ttf_audio_ms,
        "tts_total_ms": tts_total_ms,
        "backend_processing_ms": backend_processing_ms,
        "backend_other_ms": round(max(0, backend_processing_ms - stage_total_ms), 1),
    }
    return PipelineTurnResponse(
        session_id=session.session_id,
        turn_id=turn_id,
        transcript=transcript or "",
        response_text=response_text,
        response_mode=agent_result["response_mode"],
        has_tool_call=agent_result["has_tool_call"],
        audio_base64=encoded_audio,
        audio_media_type="audio/mpeg",
        tool_result=agent_result["tool_result"],
        tool_calls=agent_result["tool_calls"],
        metrics=metrics,
    )


@app.post("/latency/turn", status_code=201)
def record_latency(sample: LatencySample) -> dict:
    try:
        conversations.get_session(sample.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id")
    recorded = latency.add(sample)
    return {"recorded": recorded, "turn_id": sample.turn_id}


@app.get("/latency/summary")
def latency_summary() -> dict:
    return latency.summary()


@app.get("/latency/samples")
def latency_samples() -> list[dict]:
    return [sample.model_dump() for sample in latency.samples()]


@app.get("/latency/export.csv")
def export_latency_csv() -> Response:
    output = io.StringIO()
    fieldnames = list(LatencySample.model_fields)
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for sample in latency.samples():
        writer.writerow(sample.model_dump())
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=latency_samples.csv"},
    )


# CURRENT APPROACH:
# The frontend will ask for a short-lived Realtime client secret, then connect
# directly to OpenAI over WebRTC for low-latency voice input/output.
#
# OLD APPROACH:
# The backend directly handled /stt -> /chat -> /tts in separate REST calls.
# We are keeping that older path below during the migration.
@app.post("/realtime/session", response_model=RealtimeSessionResponse)
def create_realtime_session() -> RealtimeSessionResponse:
    try:
        realtime = create_realtime_client_secret()
    except RealtimeConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        response_mode = get_response_mode()
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    app_session = conversations.create_session()
    traces.activate_session(app_session.session_id)

    traces.emit(
        TraceEvent(
            session_id=app_session.session_id,
            type="session",
            message="App session created for realtime conversation",
            data={"mode": "realtime_webrtc", "response_mode": response_mode},
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
        response_mode=response_mode,
    )


@app.post("/realtime/tool", response_model=RealtimeToolResponse)
def execute_realtime_tool(req: RealtimeToolRequest) -> RealtimeToolResponse:
    try:
        session = conversations.get_session(req.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown session_id")

    try:
        result = realtime_tools.execute(
            session=session,
            tool_name=req.tool_name,
            tool_args=req.tool_args,
        )
    except (RealtimeToolError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    traces.emit(
        TraceEvent(
            session_id=session.session_id,
            type="response_strategy",
            message=(
                f"Directive selected: {result['directive']['key']}"
                if result["directive"]
                else f"Tool transition selected: {result['action']}"
            ),
            data={
                "purpose": result["purpose"],
                "action": result["action"],
                "response_mode": result["response_mode"],
                "directive": result["directive"],
                "information": result["information"],
                "backend_tool_ms": result["backend_tool_ms"],
            },
        )
    )

    return RealtimeToolResponse(**result)


# LEGACY ENDPOINT:
# This route is still useful for testing the old text-only orchestration path,
# but it is no longer the main direction for the natural voice experience.
@app.post("/sessions", response_model=CreateSessionResponse)
def create_session() -> CreateSessionResponse:
    session = conversations.create_session()
    traces.activate_session(session.session_id)
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
    try:
        text = stt.transcribe(data, content_type=content_type)
    except stt.SpeechToTextError as exc:
        return {"text": None, "error": str(exc)}
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

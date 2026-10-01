"""OpenAI realtime transcription bridge for the streaming pipeline."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any


class RealtimeTranscriptionError(RuntimeError):
    """Raised when the live transcription session cannot be established."""


class RealtimeTranscriber:
    """Forward PCM16 frames to a realtime transcription session.

    The browser sends raw mono PCM16 at 24 kHz. OpenAI emits partial transcript
    deltas while frames are arriving and a completed transcript after commit.
    """

    def __init__(self, websocket: Any, *, on_delta: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.websocket = websocket
        self.on_delta = on_delta
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None

    @classmethod
    async def connect(cls, *, on_delta: Callable[[str], Awaitable[None]] | None = None) -> "RealtimeTranscriber":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RealtimeTranscriptionError("OPENAI_API_KEY is not set")
        try:
            import websockets
        except ImportError as exc:
            raise RealtimeTranscriptionError("Install websockets to use streaming STT") from exc

        url = os.environ.get(
            "OPENAI_TRANSCRIPTION_WS_URL",
            "wss://api.openai.com/v1/realtime?intent=transcription",
        )
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        connection_options = {
            "open_timeout": float(os.environ.get("STT_REALTIME_CONNECT_TIMEOUT_SECONDS", "10")),
            "close_timeout": 5,
            "ping_interval": 20,
            # Avoid indefinite Windows system-proxy discovery. The existing
            # REST providers also connect directly in this local demo.
            "proxy": None,
        }
        try:
            try:
                socket = await websockets.connect(
                    url,
                    additional_headers=headers,
                    **connection_options,
                )
            except TypeError:
                socket = await websockets.connect(
                    url,
                    extra_headers=headers,
                    **connection_options,
                )
        except Exception as exc:
            raise RealtimeTranscriptionError(f"Could not connect to realtime transcription: {exc}") from exc

        bridge = cls(socket, on_delta=on_delta)
        bridge._reader_task = asyncio.create_task(bridge._read_events())
        await bridge._send({
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24000},
                        "transcription": {
                            "model": os.environ.get("STT_REALTIME_MODEL", "gpt-live-transcribe"),
                            "language": os.environ.get("STT_LANGUAGE", "en").strip() or "en",
                            "prompt": os.environ.get("STT_PROMPT", "").strip() or (
                                "Transcribe this English customer-support call. Preserve names and "
                                "order or ticket identifiers exactly as spoken. Use Latin characters."
                            ),
                            "delay": os.environ.get("STT_REALTIME_DELAY", "low"),
                        },
                        "turn_detection": None,
                    }
                },
            },
        })
        return bridge

    async def _send(self, event: dict[str, Any]) -> None:
        await self.websocket.send(json.dumps(event, separators=(",", ":")))

    async def _read_events(self) -> None:
        try:
            async for raw in self.websocket:
                event = json.loads(raw)
                event_type = event.get("type", "")
                if event_type == "conversation.item.input_audio_transcription.delta":
                    delta = str(event.get("delta") or "")
                    if delta and self.on_delta:
                        await self.on_delta(delta)
                await self._events.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._events.put({"type": "error", "error": {"message": str(exc)}})

    async def append(self, pcm16: bytes) -> None:
        if pcm16:
            await self._send({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm16).decode("ascii"),
            })

    async def commit(self) -> None:
        await self._send({"type": "input_audio_buffer.commit"})

    async def final_transcript(self) -> str:
        while True:
            event = await self._events.get()
            event_type = event.get("type", "")
            if event_type == "conversation.item.input_audio_transcription.completed":
                return str(event.get("transcript") or "").strip()
            if event_type == "error":
                detail = event.get("error", {}).get("message", "Realtime transcription failed")
                raise RealtimeTranscriptionError(str(detail))

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        try:
            await self.websocket.close()
        except Exception:
            pass

"""Persistent text-in/audio-out TTS sessions for the streaming pipeline."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import urlencode


class StreamingTTSError(RuntimeError):
    """Raised when a streaming TTS session cannot be used."""


class DeepgramFluxSession:
    """One persistent Deepgram Flux TTS WebSocket per browser call."""

    def __init__(self, websocket: Any) -> None:
        self.websocket = websocket
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None

    @classmethod
    async def connect(cls) -> "DeepgramFluxSession":
        api_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
        if not api_key:
            raise StreamingTTSError(
                "DEEPGRAM_API_KEY is required when STREAMING_TTS_PROVIDER=deepgram"
            )

        try:
            import websockets
        except ImportError as exc:
            raise StreamingTTSError("Install websockets to use Deepgram streaming TTS") from exc

        model = os.environ.get("STREAMING_TTS_MODEL", "flux-alexis-en").strip()
        if not model.startswith("flux-"):
            raise StreamingTTSError("STREAMING_TTS_MODEL must be a Deepgram Flux model")

        query = urlencode(
            {
                "model": model,
                "encoding": "linear16",
                "sample_rate": "24000",
            }
        )
        url = f"wss://api.deepgram.com/v2/speak?{query}"
        headers = {"Authorization": f"Token {api_key}"}
        connection_options = {
            "open_timeout": float(os.environ.get("STREAMING_TTS_CONNECT_TIMEOUT_SECONDS", "10")),
            "close_timeout": 5,
            # Deepgram closes an idle Flux session after 60 seconds. WebSocket
            # pings keep the call-scoped connection warm between user turns.
            "ping_interval": 20,
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
            raise StreamingTTSError(f"Could not connect to Deepgram streaming TTS: {exc}") from exc

        session = cls(socket)
        session._reader_task = asyncio.create_task(session._read_events())
        return session

    async def _send(self, event: dict[str, Any]) -> None:
        try:
            await self.websocket.send(json.dumps(event, separators=(",", ":")))
        except Exception as exc:
            raise StreamingTTSError(f"Could not send text to Deepgram TTS: {exc}") from exc

    async def send_text(self, text: str) -> None:
        """Append an LLM delta to the active speech turn without client chunking."""
        if text:
            await self._send({"type": "Speak", "text": text})

    async def flush(self) -> None:
        """End the active response after the final LLM delta has been sent."""
        await self._send({"type": "Flush"})

    async def _read_events(self) -> None:
        try:
            async for raw in self.websocket:
                if isinstance(raw, bytes):
                    if raw:
                        await self._events.put({"type": "audio", "audio": raw})
                    continue
                try:
                    event = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    await self._events.put(
                        {"type": "Error", "description": "Deepgram returned an invalid event"}
                    )
                    continue
                await self._events.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._events.put({"type": "Error", "description": str(exc)})

    def next_event_nowait(self) -> dict[str, Any] | None:
        try:
            return self._events.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def next_event(self, *, timeout: float = 30.0) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(self._events.get(), timeout=timeout)
        except TimeoutError as exc:
            raise StreamingTTSError("Timed out waiting for Deepgram TTS audio") from exc

    async def close(self) -> None:
        try:
            await self._send({"type": "Close"})
        except StreamingTTSError:
            pass
        if self._reader_task:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        try:
            await self.websocket.close()
        except Exception:
            pass


def get_streaming_tts_provider() -> str:
    provider = os.environ.get("STREAMING_TTS_PROVIDER", "openai").strip().lower()
    if provider not in {"deepgram", "openai"}:
        raise StreamingTTSError(
            "STREAMING_TTS_PROVIDER must be 'deepgram' or 'openai'"
        )
    return provider

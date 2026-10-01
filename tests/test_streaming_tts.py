from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import patch

from backend.streaming_tts import (
    DeepgramFluxSession,
    StreamingTTSError,
    get_streaming_tts_provider,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[bytes | str] = asyncio.Queue()
        self.sent: list[str] = []
        self.closed = False

    def __aiter__(self) -> "FakeWebSocket":
        return self

    async def __anext__(self) -> bytes | str:
        return await self.incoming.get()

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True


class DeepgramFluxSessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.socket = FakeWebSocket()
        self.session = DeepgramFluxSession(self.socket)
        self.session._reader_task = asyncio.create_task(self.session._read_events())

    async def asyncTearDown(self) -> None:
        await self.session.close()

    async def test_streams_exact_llm_deltas_then_flushes_once(self) -> None:
        await self.session.send_text("Sure, ")
        await self.session.send_text("I can help.")
        await self.session.flush()

        self.assertEqual(
            [json.loads(payload) for payload in self.socket.sent],
            [
                {"type": "Speak", "text": "Sure, "},
                {"type": "Speak", "text": "I can help."},
                {"type": "Flush"},
            ],
        )

    async def test_exposes_binary_audio_and_completion_events_in_order(self) -> None:
        await self.socket.incoming.put(b"\x01\x02")
        await self.socket.incoming.put(json.dumps({"type": "SpeechMetadata"}))

        audio = await self.session.next_event()
        completed = await self.session.next_event()

        self.assertEqual(audio, {"type": "audio", "audio": b"\x01\x02"})
        self.assertEqual(completed, {"type": "SpeechMetadata"})


class StreamingTTSProviderTest(unittest.TestCase):
    def test_defaults_to_openai(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_streaming_tts_provider(), "openai")

    def test_rejects_unknown_provider(self) -> None:
        with patch.dict(os.environ, {"STREAMING_TTS_PROVIDER": "unknown"}, clear=True):
            with self.assertRaises(StreamingTTSError):
                get_streaming_tts_provider()


if __name__ == "__main__":
    unittest.main()

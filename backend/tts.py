"""Text-to-speech helpers."""

from __future__ import annotations

import os
import time
from typing import Iterator, Optional

_openai_client = None


def _client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=api_key)
        return _openai_client
    except Exception:
        return None


def synthesize(text: str) -> Optional[bytes]:
    """Synthesize speech from text. Returns MP3 bytes or None if not configured."""
    client = _client()
    if not client:
        return None
    try:
        r = client.audio.speech.create(
            model=os.environ.get("TTS_MODEL", "gpt-4o-mini-tts"),
            voice=os.environ.get("TTS_VOICE", "marin"),
            input=text,
        )
        return r.content
    except Exception:
        return None


def synthesize_with_timing(text: str) -> tuple[Optional[bytes], Optional[float], Optional[float]]:
    """Return audio bytes plus approximate first-byte and total TTS timing."""
    client = _client()
    if not client:
        return None, None, None

    started_at = time.perf_counter()
    first_audio_ms: Optional[float] = None
    chunks: list[bytes] = []
    try:
        with client.audio.speech.with_streaming_response.create(
            model=os.environ.get("TTS_MODEL", "gpt-4o-mini-tts"),
            voice=os.environ.get("TTS_VOICE", "marin"),
            input=text,
            response_format="mp3",
        ) as response:
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                if first_audio_ms is None:
                    first_audio_ms = round((time.perf_counter() - started_at) * 1000, 1)
                chunks.append(chunk)
    except Exception:
        return None, None, None

    total_ms = round((time.perf_counter() - started_at) * 1000, 1)
    return b"".join(chunks), first_audio_ms, total_ms


def stream_with_timing(
    text: str,
    *,
    response_format: str = "mp3",
) -> Iterator[tuple[bytes, Optional[float], Optional[float]]]:
    """Yield TTS audio chunks with first-audio and final-total timing markers.

    Each yielded tuple is ``(chunk, first_audio_ms, total_ms)``. ``first_audio_ms``
    is populated only on the first non-empty audio chunk. ``total_ms`` is
    populated only on the final sentinel yield, where ``chunk`` is empty.
    """
    client = _client()
    if not client:
        return

    started_at = time.perf_counter()
    first_audio_ms: Optional[float] = None
    with client.audio.speech.with_streaming_response.create(
        model=os.environ.get("TTS_MODEL", "gpt-4o-mini-tts"),
        voice=os.environ.get("TTS_VOICE", "marin"),
        input=text,
        response_format=response_format,
    ) as response:
        for chunk in response.iter_bytes():
            if not chunk:
                continue
            current_first_audio_ms = None
            if first_audio_ms is None:
                first_audio_ms = round((time.perf_counter() - started_at) * 1000, 1)
                current_first_audio_ms = first_audio_ms
            yield chunk, current_first_audio_ms, None

    total_ms = round((time.perf_counter() - started_at) * 1000, 1)
    yield b"", None, total_ms


def is_available() -> bool:
    return _client() is not None

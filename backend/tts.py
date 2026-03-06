"""Text-to-speech: optional OpenAI TTS. If OPENAI_API_KEY is missing, returns None."""

from __future__ import annotations

import os
from typing import Optional

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
        r = client.audio.speech.create(model="tts-1", voice="shimmer", input=text)
        return r.content
    except Exception:
        return None


def is_available() -> bool:
    return _client() is not None

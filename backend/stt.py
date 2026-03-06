"""Speech-to-text: optional OpenAI Whisper. If OPENAI_API_KEY is missing, returns None."""

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


def transcribe(audio_bytes: bytes, *, content_type: str = "audio/webm") -> Optional[str]:
    """Transcribe audio to text. Returns None if Whisper is not configured."""
    client = _client()
    if not client:
        return None
    try:
        import io
        file = io.BytesIO(audio_bytes)
        file.name = "audio.webm"
        r = client.audio.transcriptions.create(model="whisper-1", file=file)
        return (r.text or "").strip() or None
    except Exception:
        return None


def is_available() -> bool:
    return _client() is not None

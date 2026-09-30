"""Speech-to-text helpers."""

from __future__ import annotations

import os
import io
import math
import re
import time
import wave
from typing import Optional

_openai_client = None


class SpeechToTextError(RuntimeError):
    """Raised when a configured transcription request cannot be completed."""


_AUDIO_UPLOAD_TYPES = {
    "audio/flac": ("flac", "audio/flac"),
    "audio/m4a": ("m4a", "audio/m4a"),
    "audio/mp4": ("mp4", "audio/mp4"),
    "audio/mpeg": ("mp3", "audio/mpeg"),
    "audio/mp3": ("mp3", "audio/mpeg"),
    "audio/mpga": ("mpga", "audio/mpeg"),
    "audio/ogg": ("ogg", "audio/ogg"),
    "audio/wav": ("wav", "audio/wav"),
    "audio/wave": ("wav", "audio/wav"),
    "audio/x-wav": ("wav", "audio/wav"),
    "audio/webm": ("webm", "audio/webm"),
}

_DEFAULT_PROMPT = (
    "Transcribe an English customer-success phone call for Mira. Preserve names in English "
    "script. Write clearly spoken numbers, phone numbers, order IDs, and ticket IDs using "
    "digits. Always output Latin characters only; transliterate any recognized names into "
    "English script instead of native scripts. The caller is expected to speak English; for "
    "short acknowledgements, callback confirmations, and support requests, prefer English "
    "words over Hindi or Hinglish. Do not translate, summarize, or invent speech."
)

_FIELD_PROMPTS = {
    "caller_name": "The caller is stating their name. Output the name in Latin characters.",
    "caller_phone": "The caller is stating phone digits. Format only spoken digits as numerals.",
    "order_id": "The caller is stating an order ID made of digits. Format spoken digits as numerals.",
    "ticket_id": "The caller is stating a ticket ID made of digits. Format spoken digits as numerals.",
    "callback_time": "The caller is stating their preferred callback day and time.",
}

def normalize_spoken_identifiers(text: str, *, expected_field: Optional[str] = None) -> str:
    """Return provider text without rule-based word replacement."""
    return text


def wav_duration_seconds(audio_bytes: bytes, *, content_type: str) -> Optional[float]:
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type not in {"audio/wav", "audio/wave", "audio/x-wav"}:
        return None
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            if frame_rate <= 0:
                return None
            return wav_file.getnframes() / frame_rate
    except (EOFError, wave.Error):
        return None


def transcript_is_implausible(text: str, *, audio_duration_seconds: Optional[float]) -> bool:
    """Reject transcript lengths that could not plausibly fit in the audio."""
    if not text or not audio_duration_seconds or audio_duration_seconds <= 0:
        return False
    word_count = len(re.findall(r"\b\w+\b", text, flags=re.UNICODE))
    maximum_words = max(10, math.ceil(audio_duration_seconds * 5.5) + 4)
    return word_count > maximum_words


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


def transcribe(
    audio_bytes: bytes,
    *,
    content_type: str = "audio/webm",
    expected_field: Optional[str] = None,
) -> Optional[str]:
    """Transcribe a bounded audio recording, preserving its actual upload format."""
    client = _client()
    if not client:
        raise SpeechToTextError("STT is not configured. Set OPENAI_API_KEY.")

    normalized_type = (content_type or "audio/webm").split(";", 1)[0].strip().lower()
    upload_type = _AUDIO_UPLOAD_TYPES.get(normalized_type)
    if upload_type is None:
        raise SpeechToTextError(f"Unsupported STT audio type: {content_type}")
    extension, media_type = upload_type

    try:
        request = {
            "model": os.environ.get("STT_MODEL", "gpt-4o-transcribe"),
            "file": (f"audio.{extension}", audio_bytes, media_type),
            "response_format": "json",
            "language": os.environ.get("STT_LANGUAGE", "en").strip() or "en",
        }
        configured_prompt = os.environ.get("STT_PROMPT", "").strip()
        base_prompt = configured_prompt or _DEFAULT_PROMPT
        field_prompt = _FIELD_PROMPTS.get(expected_field or "")
        prompt = f"{base_prompt} {field_prompt}".strip()
        if prompt:
            request["prompt"] = prompt

        r = client.audio.transcriptions.create(
            **request,
        )
        raw_text = (r.text or "").strip()
        return normalize_spoken_identifiers(raw_text, expected_field=expected_field) or None
    except Exception as exc:
        raise SpeechToTextError(f"STT request failed: {exc}") from exc


def transcribe_with_timing(
    audio_bytes: bytes,
    *,
    content_type: str = "audio/webm",
    expected_field: Optional[str] = None,
) -> tuple[Optional[str], float]:
    started_at = time.perf_counter()
    text = transcribe(
        audio_bytes,
        content_type=content_type,
        expected_field=expected_field,
    )
    return text, round((time.perf_counter() - started_at) * 1000, 1)


def is_available() -> bool:
    return _client() is not None

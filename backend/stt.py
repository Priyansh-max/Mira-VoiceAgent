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

_FIELD_PROMPTS = {
    "caller_name": "A caller is stating their name. Preserve the spoken name and use English script.",
    "caller_phone": "A caller is stating phone digits. Format only spoken digits as numerals.",
    "order_id": "A caller is stating an order ID made of digits. Format spoken digits as numerals.",
    "ticket_id": "A caller is stating a ticket ID made of digits. Format spoken digits as numerals.",
    "callback_time": "A caller is stating their preferred callback day and time.",
}

_DIGIT_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "زیرو": "0",
    "صفر": "0",
    "ون": "1",
    "ایک": "1",
    "ٹو": "2",
    "دو": "2",
    "تھری": "3",
    "تین": "3",
    "فور": "4",
    "چار": "4",
    "فائیو": "5",
    "پانچ": "5",
    "سکس": "6",
    "چھ": "6",
    "سیون": "7",
    "سات": "7",
    "ایٹ": "8",
    "آٹھ": "8",
    "نائن": "9",
    "نو": "9",
}
_DIGIT_ALTERNATION = "|".join(
    re.escape(word) for word in sorted(_DIGIT_WORDS, key=len, reverse=True)
)
_SPOKEN_DIGIT_SEQUENCE = re.compile(
    rf"(?<!\w)(?:{_DIGIT_ALTERNATION})(?:[\s,.-]+(?:{_DIGIT_ALTERNATION}))+(?!\w)",
    re.IGNORECASE,
)
_NON_ASCII_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹०१२३४५६७८९",
    "012345678901234567890123456789",
)


def normalize_spoken_identifiers(text: str) -> str:
    """Normalize common spoken and non-ASCII digit sequences for IDs."""
    normalized = text.translate(_NON_ASCII_DIGITS)

    def replace_sequence(match: re.Match[str]) -> str:
        words = re.findall(r"[^\W_]+", match.group(0), flags=re.UNICODE)
        return "".join(_DIGIT_WORDS[word.casefold()] for word in words)

    return _SPOKEN_DIGIT_SEQUENCE.sub(replace_sequence, normalized)


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
            "model": os.environ.get("STT_MODEL", "gpt-4o-mini-transcribe"),
            "file": (f"audio.{extension}", audio_bytes, media_type),
            "response_format": "json",
            "language": os.environ.get("STT_LANGUAGE", "en").strip() or "en",
        }
        configured_prompt = os.environ.get("STT_PROMPT", "").strip()
        prompt = _FIELD_PROMPTS.get(expected_field or "") or configured_prompt
        if prompt:
            request["prompt"] = prompt

        r = client.audio.transcriptions.create(
            **request,
        )
        raw_text = (r.text or "").strip()
        return normalize_spoken_identifiers(raw_text) or None
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

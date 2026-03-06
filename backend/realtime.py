from __future__ import annotations

import os
from typing import Any, Dict

from openai import OpenAI


class RealtimeConfigError(RuntimeError):
    """Raised when realtime bootstrap cannot be created."""


def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RealtimeConfigError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def _session_config() -> Dict[str, Any]:
    # CURRENT APPROACH:
    # The browser will connect to OpenAI Realtime over WebRTC.
    # The backend only issues a short-lived client secret plus session config.
    #
    # OLD APPROACH:
    # The backend handled /chat, /stt, and /tts as separate REST-style steps.
    # We are keeping that path temporarily while we transition.
    return {
        "type": "realtime",
        "model": os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime-mini"),
        "instructions": (
            "You are a helpful customer support voice assistant. "
            "Be concise, friendly, and natural in speech. "
            "Ask one clarifying question at a time. "
            "Do not invent account or ticket information. "
            "Tool calling will be added by the backend in a later step."
        ),
        "audio": {
            "input": {
                "noise_reduction": {"type": "near_field"},
                "transcription": {
                    "model": os.environ.get(
                        "OPENAI_REALTIME_TRANSCRIBE_MODEL",
                        "gpt-4o-mini-transcribe",
                    ),
                    "language": "en",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 550,
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {
                "voice": os.environ.get("OPENAI_REALTIME_VOICE", "marin"),
                "speed": 1.0,
            },
        },
        "output_modalities": ["audio"],
        "tracing": "auto",
    }


def _to_dict(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return obj
    raise RealtimeConfigError("Unexpected OpenAI SDK response shape")


def create_realtime_client_secret() -> Dict[str, Any]:
    try:
        client = _client()
        response = client.realtime.client_secrets.create(
            session=_session_config(),
            expires_after={"anchor": "created_at", "seconds": 600},
        )
    except Exception as exc:  # pragma: no cover - depends on external API
        raise RealtimeConfigError(f"Could not create realtime client secret: {exc}") from exc

    return _to_dict(response)

from __future__ import annotations

import os
from typing import Any, Dict

from openai import OpenAI

from backend.prompts import REALTIME_AGENT_PROMPT
from backend.tool_contract import grouped_tool_schemas


class RealtimeConfigError(RuntimeError):
    """Raised when realtime bootstrap cannot be created."""


def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RealtimeConfigError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def _tool_schemas() -> list[Dict[str, Any]]:
    # Realtime function tools use a slightly different shape than Chat
    # Completions tools and currently reject a top-level `strict` field.
    return grouped_tool_schemas(include_strict=False)


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
        "instructions": REALTIME_AGENT_PROMPT,
        "tools": _tool_schemas(),
        "tool_choice": "auto",
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
        "truncation": {
            "type": "retention_ratio",
            "retention_ratio": 0.8,
        },
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

    payload = _to_dict(response)

    # The SDK/REST response can arrive in slightly different shapes depending on
    # the endpoint/version. Normalize it here so the FastAPI route can stay simple.
    if "client_secret" in payload and isinstance(payload["client_secret"], dict):
        secret_value = payload["client_secret"].get("value")
        expires_at = payload.get("expires_at") or payload["client_secret"].get("expires_at")
        session = payload.get("session", {})
    else:
        secret_value = payload.get("value")
        expires_at = payload.get("expires_at")
        session = payload.get("session", {})

    if not secret_value:
        raise RealtimeConfigError(f"OpenAI realtime response did not include a client secret: {payload}")

    return {
        "client_secret": {"value": secret_value, "expires_at": expires_at},
        "expires_at": expires_at,
        "session": session,
    }

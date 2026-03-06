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


def _tool_schemas() -> list[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "identify_customer",
            "description": "Find a customer profile by name before accessing protected records.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The customer's name. Prefer the full name when available.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "verify_customer",
            "description": "Verify a selected customer using the last 4 digits of the phone number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The selected customer identifier from identify_customer.",
                    },
                    "phone_last4": {
                        "type": "string",
                        "description": "Last 4 digits of the customer's phone number.",
                    },
                },
                "required": ["customer_id", "phone_last4"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "lookup_ticket",
            "description": "Look up a ticket status by ticket ID after verification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {
                        "type": "string",
                        "description": "The ticket or case ID, for example 4821.",
                    }
                },
                "required": ["case_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "get_order_status",
            "description": "Look up an order status by order ID after verification.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "The order ID, for example 1234.",
                    }
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "schedule_callback",
            "description": "Schedule a callback from a human support agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "when": {
                        "type": "string",
                        "description": "Preferred callback time, such as tomorrow morning or next available time.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Short reason for the callback request.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    ]


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
            "Use a tool only when you need backend data or need to take a backend action. "
            "For greetings, simple follow-up questions, and general unsupported questions, answer directly without calling a tool. "
            "Ticket and order lookups are protected and require verification first. "
            "If a tool result includes policy_outcome, follow it closely and ask only for the missing detail when needed."
        ),
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

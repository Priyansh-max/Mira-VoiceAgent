from __future__ import annotations

from typing import Any, Dict


PURPOSES = ["order_status", "ticket_status", "customer_support"]
TOOL_NAMES = ["customer_identity", "customer_lookup", "support_callback"]


def _shared_parameters() -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "purpose": {
                "type": "string",
                "enum": PURPOSES,
                "description": (
                    "The active purpose. Reuse the existing purpose unless the caller clearly "
                    "starts a different supported request."
                ),
            },
            "caller_name": {
                "type": ["string", "null"],
                "description": "The caller's name when supplied; otherwise null.",
            },
            "caller_phone": {
                "type": ["string", "null"],
                "description": (
                    "The caller's phone number or last four digits as continuous digits with no "
                    "spoken separators; otherwise null."
                ),
            },
            "order_id": {
                "type": ["string", "null"],
                "description": (
                    "The order ID as one continuous digit string with no commas, spaces, or "
                    "spoken separators; otherwise null."
                ),
            },
            "ticket_id": {
                "type": ["string", "null"],
                "description": (
                    "The ticket ID as one continuous digit string with no commas, spaces, or "
                    "spoken separators; otherwise null."
                ),
            },
            "callback_time": {
                "type": ["string", "null"],
                "description": (
                    "The caller's requested callback day and time when supplied; otherwise null."
                ),
            },
            "attempt": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "0 before a required field has been requested; 1 after it was requested once. "
                    "Never reduce this value during the call."
                ),
            },
        },
        # Strict function schemas require every property to be present. The two
        # caller fields remain semantically optional because they accept null.
        "required": [
            "purpose",
            "caller_name",
            "caller_phone",
            "order_id",
            "ticket_id",
            "callback_time",
            "attempt",
        ],
        "additionalProperties": False,
    }


def grouped_tool_schemas(*, include_strict: bool = True) -> list[Dict[str, Any]]:
    descriptions = {
        "customer_identity": (
            "Resolve the caller's identity for the stated purpose. Call first for any protected "
            "status request, including when no caller information is available."
        ),
        "customer_lookup": (
            "Look up the current request after customer_identity returns action ready_for_lookup."
        ),
        "support_callback": (
            "Request or schedule a human-support callback. Use this tool both to ask for the "
            "preferred callback day and time and, after the caller answers, to schedule it."
        ),
    }
    schemas = [
        {
            "type": "function",
            "name": name,
            "description": descriptions[name],
            "parameters": _shared_parameters(),
        }
        for name in TOOL_NAMES
    ]
    if include_strict:
        for schema in schemas:
            schema["strict"] = True
    return schemas

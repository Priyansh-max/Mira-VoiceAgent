from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Iterable, Optional

from openai import OpenAI

from backend.config import get_response_mode
from backend.conversation import SessionState, expected_information_field
from backend.prompts import REALTIME_AGENT_PROMPT
from backend.realtime_tools import RealtimeToolRouter
from backend.tool_contract import grouped_tool_schemas


class PipelineAgentConfigError(RuntimeError):
    """Raised when the chained voice pipeline cannot run."""


def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise PipelineAgentConfigError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


def _chat_tool_schemas() -> list[Dict[str, Any]]:
    tools = []
    for schema in grouped_tool_schemas():
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema["description"],
                    "parameters": schema["parameters"],
                    "strict": schema.get("strict", True),
                },
            }
        )
    return tools


def _history_messages(session: SessionState) -> list[Dict[str, str]]:
    messages: list[Dict[str, str]] = []
    for turn in session.conversation_history[-8:]:
        role = turn.get("role")
        text = turn.get("text")
        if role in {"user", "assistant"} and isinstance(text, str) and text.strip():
            messages.append({"role": role, "content": text.strip()})
    return messages


def _compact_tool_result(tool_result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "purpose": tool_result.get("purpose"),
        "action": tool_result.get("action"),
        "caller_name": tool_result.get("caller_name"),
        "caller_phone": tool_result.get("caller_phone"),
        "order_id": tool_result.get("order_id"),
        "ticket_id": tool_result.get("ticket_id"),
        "callback_time": tool_result.get("callback_time"),
        "attempt": tool_result.get("attempt"),
        "information": tool_result.get("information"),
        "directive": tool_result.get("directive"),
        "response_mode": tool_result.get("response_mode"),
        "backend_tool_ms": tool_result.get("backend_tool_ms"),
    }


_EXPLICIT_REFUSAL = re.compile(
    r"\b(?:do not|don't|dont|won't|will not|cannot|can't|cant)"
    r"(?:\s+want\s+to)?\s+(?:share|give|provide|say)\b|"
    r"\b(?:rather not|prefer not)\b",
    re.IGNORECASE,
)
_ORDER_REQUEST = re.compile(
    r"\b(?:order\s+(?:status|tracking)|status\s+of\s+(?:my\s+)?order|"
    r"track\s+(?:my\s+)?order|where\s+is\s+(?:my\s+)?order)\b",
    re.IGNORECASE,
)
_TICKET_REQUEST = re.compile(
    r"\b(?:ticket\s+(?:status|update)|status\s+of\s+(?:my\s+)?ticket|case\s+status)\b",
    re.IGNORECASE,
)
_HUMAN_SUPPORT_REQUEST = re.compile(
    r"\b(?:speak|talk)\s+to\s+(?:a\s+)?(?:person|human|agent)|"
    r"\b(?:schedule|arrange|request)\s+(?:a\s+)?callback\b",
    re.IGNORECASE,
)


def _forced_tool_for_turn(session: SessionState, text: str) -> Optional[str]:
    if session.pending_intent and _EXPLICIT_REFUSAL.search(text):
        return "support_callback"

    expected_field = expected_information_field(session)
    if expected_field in {"caller_name", "caller_phone"}:
        return "customer_identity"
    if expected_field in {"order_id", "ticket_id"}:
        return "customer_lookup"
    if expected_field == "callback_time":
        return "support_callback"

    if _ORDER_REQUEST.search(text):
        return "customer_lookup" if session.verified else "customer_identity"
    if _TICKET_REQUEST.search(text):
        return "customer_lookup" if session.verified else "customer_identity"
    if _HUMAN_SUPPORT_REQUEST.search(text):
        return "support_callback"
    return None


def _extract_tool_calls(
    chunks: Iterable[Any],
    *,
    started_at: Optional[float] = None,
) -> tuple[str, list[Dict[str, Any]], Optional[float], float]:
    # The caller starts this clock before opening the provider stream so TTFT
    # includes request setup, network time, and the wait for the first delta.
    started_at = started_at or time.perf_counter()
    first_delta_ms: Optional[float] = None
    text_parts: list[str] = []
    tool_calls_by_index: dict[int, Dict[str, Any]] = {}

    for chunk in chunks:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            if first_delta_ms is None:
                first_delta_ms = round((time.perf_counter() - started_at) * 1000, 1)
            text_parts.append(content)

        for tool_call in getattr(delta, "tool_calls", None) or []:
            if first_delta_ms is None:
                first_delta_ms = round((time.perf_counter() - started_at) * 1000, 1)
            index = int(tool_call.index or 0)
            existing = tool_calls_by_index.setdefault(
                index,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if getattr(tool_call, "id", None):
                existing["id"] += tool_call.id
            function_delta = getattr(tool_call, "function", None)
            if function_delta:
                if getattr(function_delta, "name", None):
                    existing["function"]["name"] += function_delta.name
                if getattr(function_delta, "arguments", None):
                    existing["function"]["arguments"] += function_delta.arguments

    total_ms = round((time.perf_counter() - started_at) * 1000, 1)
    return "".join(text_parts).strip(), list(tool_calls_by_index.values()), first_delta_ms, total_ms


class ChainedPipelineAgent:
    def __init__(self) -> None:
        self.model = os.environ.get("LLM_MODEL", os.environ.get("OPENAI_TEXT_MODEL", "gpt-4o-mini"))
        self.tools = _chat_tool_schemas()
        self.router = RealtimeToolRouter()

    def handle_text(self, *, session: SessionState, text: str) -> Dict[str, Any]:
        response_mode = get_response_mode()
        messages = [
            {"role": "system", "content": REALTIME_AGENT_PROMPT},
            *_history_messages(session),
            {"role": "user", "content": text},
        ]

        try:
            llm_started_at = time.perf_counter()
            forced_tool = _forced_tool_for_turn(session, text)
            tool_choice: Any = "auto"
            if forced_tool:
                tool_choice = {
                    "type": "function",
                    "function": {"name": forced_tool},
                }
            stream = _client().chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self.tools,
                tool_choice=tool_choice,
                temperature=0.2,
                stream=True,
            )
            assistant_text, tool_calls, llm_ttft_ms, llm_total_ms = _extract_tool_calls(
                stream,
                started_at=llm_started_at,
            )
        except Exception as exc:
            raise PipelineAgentConfigError(f"OpenAI pipeline planning call failed: {exc}") from exc

        if not tool_calls:
            final_text = assistant_text or "I heard you, but I need a little more detail to help."
            self._append_history(session=session, user_text=text, assistant_text=final_text)
            return {
                "response_text": final_text,
                "response_mode": response_mode,
                "has_tool_call": False,
                "tool_result": None,
                "tool_calls": [],
                "llm_ttft_ms": llm_ttft_ms,
                "llm_total_ms": llm_total_ms,
                "tool_round_trip_ms": None,
            }

        tool_started_at = time.perf_counter()
        tool_result, tool_events = self._run_tool_chain(session=session, tool_calls=tool_calls)
        tool_round_trip_ms = round((time.perf_counter() - tool_started_at) * 1000, 1)

        if response_mode == "speech_directive":
            directive_text = (tool_result.get("directive") or {}).get("response_text")
            if not directive_text:
                raise PipelineAgentConfigError(
                    f"Tool action {tool_result.get('action')} did not return a directive"
                )
            self._append_history(session=session, user_text=text, assistant_text=directive_text)
            return {
                "response_text": directive_text,
                "response_mode": response_mode,
                "has_tool_call": True,
                "tool_result": _compact_tool_result(tool_result),
                "tool_calls": tool_events,
                "llm_ttft_ms": llm_ttft_ms,
                "llm_total_ms": llm_total_ms,
                "tool_round_trip_ms": tool_round_trip_ms,
            }

        final_text, second_ttft_ms, second_total_ms = self._compose_tool_response(
            messages=messages,
            tool_calls=tool_calls,
            tool_result=tool_result,
        )
        combined_ttft_ms = llm_ttft_ms
        combined_total_ms = round((llm_total_ms or 0) + second_total_ms, 1)
        self._append_history(session=session, user_text=text, assistant_text=final_text)
        return {
            "response_text": final_text,
            "response_mode": response_mode,
            "has_tool_call": True,
            "tool_result": _compact_tool_result(tool_result),
            "tool_calls": tool_events,
            "llm_ttft_ms": combined_ttft_ms or second_ttft_ms,
            "llm_total_ms": combined_total_ms,
            "tool_round_trip_ms": tool_round_trip_ms,
        }

    def handle_unrecognized_capture(
        self,
        *,
        session: SessionState,
        expected_field: str,
    ) -> Dict[str, Any]:
        """Produce the configured repair directive without inventing user text."""
        tool_name = {
            "caller_name": "customer_identity",
            "caller_phone": "customer_identity",
            "order_id": "customer_lookup",
            "ticket_id": "customer_lookup",
            "callback_time": "support_callback",
        }.get(expected_field)
        if not tool_name:
            raise PipelineAgentConfigError(f"Unsupported recognition field: {expected_field}")

        tool_input = {
            "purpose": session.pending_intent or "customer_support",
            "caller_name": session.claimed_name,
            "caller_phone": session.phone_last4,
            "order_id": session.order_id,
            "ticket_id": session.ticket_id,
            "callback_time": session.callback_time,
            "attempt": 1,
        }
        tool_call = {
            "id": f"recognition_repair_{int(time.time() * 1000)}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(tool_input),
            },
        }
        tool_started_at = time.perf_counter()
        tool_result, tool_events = self._run_tool_chain(
            session=session,
            tool_calls=[tool_call],
        )
        tool_round_trip_ms = round((time.perf_counter() - tool_started_at) * 1000, 1)
        directive_text = (tool_result.get("directive") or {}).get("response_text")
        if not directive_text:
            raise PipelineAgentConfigError(
                f"Recognition repair action {tool_result.get('action')} did not return a directive"
            )
        session.conversation_history.append({"role": "assistant", "text": directive_text})
        session.conversation_history[:] = session.conversation_history[-16:]
        return {
            "response_text": directive_text,
            "response_mode": get_response_mode(),
            "has_tool_call": True,
            "tool_result": _compact_tool_result(tool_result),
            "tool_calls": tool_events,
            "llm_ttft_ms": None,
            "llm_total_ms": 0.0,
            "tool_round_trip_ms": tool_round_trip_ms,
        }

    def _run_tool_chain(
        self,
        *,
        session: SessionState,
        tool_calls: list[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
        current = tool_calls[0]
        name = current["function"]["name"]
        args = self._parse_arguments(current["function"].get("arguments") or "{}")
        result = self.router.execute(session=session, tool_name=name, tool_args=args)
        tool_events = [
            {
                "tool_name": name,
                "input": args,
                "response": _compact_tool_result(result),
            }
        ]
        chained_calls = set()

        while result["action"] in {"ready_for_lookup", "route_to_callback", "route_to_identity"}:
            next_tool = {
                "ready_for_lookup": "customer_lookup",
                "route_to_callback": "support_callback",
                "route_to_identity": "customer_identity",
            }[result["action"]]
            chain_key = f"{next_tool}:{result['purpose']}:{result.get('attempt', 0)}"
            if chain_key in chained_calls:
                raise PipelineAgentConfigError("Tool continuation loop detected")
            chained_calls.add(chain_key)
            next_args = {
                "purpose": result["purpose"],
                "caller_name": result.get("caller_name") or None,
                "caller_phone": result.get("caller_phone") or None,
                "order_id": result.get("order_id") or None,
                "ticket_id": result.get("ticket_id") or None,
                "callback_time": result.get("callback_time") or None,
                "attempt": result.get("attempt", 0),
            }
            result = self.router.execute(session=session, tool_name=next_tool, tool_args=next_args)
            tool_events.append(
                {
                    "tool_name": next_tool,
                    "input": next_args,
                    "response": _compact_tool_result(result),
                }
            )

        return result, tool_events

    def _compose_tool_response(
        self,
        *,
        messages: list[Dict[str, Any]],
        tool_calls: list[Dict[str, Any]],
        tool_result: Dict[str, Any],
    ) -> tuple[str, Optional[float], float]:
        call = tool_calls[0]
        call_id = call.get("id") or f"pipeline_tool_{int(time.time() * 1000)}"
        assistant_tool_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": call["function"]["name"],
                        "arguments": call["function"]["arguments"],
                    },
                }
            ],
        }
        tool_message = {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(_compact_tool_result(tool_result)),
        }
        try:
            llm_started_at = time.perf_counter()
            stream = _client().chat.completions.create(
                model=self.model,
                messages=[*messages, assistant_tool_message, tool_message],
                tools=self.tools,
                tool_choice="none",
                temperature=0.2,
                stream=True,
            )
            text, _, ttft_ms, total_ms = _extract_tool_calls(
                stream,
                started_at=llm_started_at,
            )
        except Exception as exc:
            raise PipelineAgentConfigError(f"OpenAI post-tool response call failed: {exc}") from exc

        cleaned = text.strip()
        if not cleaned:
            directive_text = (tool_result.get("directive") or {}).get("response_text")
            cleaned = directive_text or "I handled that request."
        return cleaned, ttft_ms, total_ms

    @staticmethod
    def _parse_arguments(arguments: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {
            "purpose": parsed.get("purpose"),
            "caller_name": parsed.get("caller_name"),
            "caller_phone": parsed.get("caller_phone"),
            "order_id": parsed.get("order_id"),
            "ticket_id": parsed.get("ticket_id"),
            "callback_time": parsed.get("callback_time"),
            "attempt": parsed.get("attempt"),
        }

    @staticmethod
    def _append_history(*, session: SessionState, user_text: str, assistant_text: str) -> None:
        session.conversation_history.append({"role": "user", "text": user_text})
        session.conversation_history.append({"role": "assistant", "text": assistant_text})
        session.conversation_history[:] = session.conversation_history[-16:]

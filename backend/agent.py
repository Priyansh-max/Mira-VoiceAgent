from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from backend.conversation import SessionState
from backend.tools import get_order_status, lookup_ticket, schedule_callback
from backend.trace import TraceEvent, TraceStore


def detect_sentiment(text: str) -> str:
    t = text.lower()
    negative = ["frustrating", "angry", "upset", "terrible", "hate", "delay", "annoying"]
    positive = ["thanks", "thank you", "great", "awesome", "perfect", "love"]

    if any(w in t for w in negative):
        return "negative"
    if any(w in t for w in positive):
        return "positive"
    return "neutral"


def detect_intent_and_entities(text: str) -> Tuple[str, Dict[str, str]]:
    t = text.lower()

    # schedule callback
    if "schedule" in t and "callback" in t or t.strip().startswith("callback"):
        when = "tomorrow" if "tomorrow" in t else ("today" if "today" in t else "next available time")
        return "schedule_callback", {"time": when}

    # ticket lookup
    m = re.search(r"(ticket|case)\s*(\d+)", t)
    if m:
        return "lookup_ticket", {"ticket_id": m.group(2)}

    # order status
    m = re.search(r"order\s*(\d+)", t)
    if m:
        return "order_status", {"order_id": m.group(1)}

    return "general_query", {}


def extract_user_name(text: str) -> Optional[str]:
    t = text.strip()
    m = re.search(r"\b(this is|i am)\s+([A-Za-z][A-Za-z\-']{1,30})\b", t, flags=re.IGNORECASE)
    if m:
        return m.group(2)
    return None


class AgentOrchestrator:
    def handle_text(self, *, session: SessionState, text: str, trace: TraceStore) -> str:
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="user_input",
                message="User input received",
                data={"text": text},
            )
        )

        maybe_name = extract_user_name(text)
        if maybe_name and not session.user_name:
            session.user_name = maybe_name
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="user_identified",
                    message=f"User identified: {session.user_name}",
                    data={"user_name": session.user_name},
                )
            )

        intent, entities = detect_intent_and_entities(text)
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="intent",
                message=f"Intent detected: {intent}",
                data={"intent": intent, "entities": entities},
            )
        )

        sentiment = detect_sentiment(text)
        session.sentiment = sentiment
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="sentiment",
                message=f"Sentiment: {sentiment}",
                data={"sentiment": sentiment},
            )
        )

        tool_result: Optional[Dict[str, Any]] = None

        if intent == "lookup_ticket":
            ticket_id = entities.get("ticket_id")
            if ticket_id:
                session.ticket_id = ticket_id
                trace.emit(
                    TraceEvent(
                        session_id=session.session_id,
                        type="tool_call",
                        message=f"Tool called: lookup_ticket({ticket_id})",
                        data={"tool": "lookup_ticket", "args": {"case_id": ticket_id}},
                    )
                )
                tool_result = lookup_ticket(ticket_id)

        elif intent == "order_status":
            order_id = entities.get("order_id")
            if order_id:
                trace.emit(
                    TraceEvent(
                        session_id=session.session_id,
                        type="tool_call",
                        message=f"Tool called: get_order_status({order_id})",
                        data={"tool": "get_order_status", "args": {"order_id": order_id}},
                    )
                )
                tool_result = get_order_status(order_id)

        elif intent == "schedule_callback":
            when = entities.get("time", "next available time")
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_call",
                    message=f"Tool called: schedule_callback({when})",
                    data={"tool": "schedule_callback", "args": {"when": when}},
                )
            )
            tool_result = schedule_callback(when)

        if tool_result is not None:
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_result",
                    message="Tool result received",
                    data={"result": tool_result},
                )
            )

        response = self._generate_response(session=session, intent=intent, tool_result=tool_result)
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="response",
                message="Response generated",
                data={"text": response},
            )
        )

        session.conversation_history.append({"role": "user", "text": text})
        session.conversation_history.append({"role": "assistant", "text": response})
        return response

    def _generate_response(self, *, session: SessionState, intent: str, tool_result: Optional[Dict[str, Any]]) -> str:
        name = session.user_name
        prefix = f"Hello {name}. " if name else ""

        if session.sentiment == "negative":
            prefix = "I'm sorry about that. " + prefix

        if intent == "lookup_ticket" and tool_result:
            case_id = tool_result.get("case_id", session.ticket_id or "")
            status = tool_result.get("status", "Unknown")
            return f"{prefix}I found your ticket {case_id}. Status: {status}. Would you like me to schedule a callback?"

        if intent == "order_status" and tool_result:
            order_id = tool_result.get("order_id", "")
            status = tool_result.get("status", "Unknown")
            return f"{prefix}Your order {order_id} is: {status}. Anything else I can check?"

        if intent == "schedule_callback" and tool_result:
            when = tool_result.get("time", "soon")
            return f"{prefix}Done — your callback is scheduled for {when}. Would you like to add any notes?"

        return f"{prefix}How can I help you today?"
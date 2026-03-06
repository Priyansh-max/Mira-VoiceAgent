from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

from openai import OpenAI

from backend.conversation import SessionState
from backend.tools import (
    get_order_status,
    identify_customer,
    lookup_ticket,
    resolve_customer_identity,
    schedule_callback,
    verify_customer,
)
from backend.trace import TraceEvent, TraceStore


class TextAgentConfigError(RuntimeError):
    """Raised when the OpenAI-backed text agent cannot run."""


def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise TextAgentConfigError("OPENAI_API_KEY is not set")
    return OpenAI(api_key=api_key)


class OpenAITextAgent:
    # OLD:
    # `backend/agent.py` used regex and keyword rules to infer intent/sentiment.
    #
    # CURRENT:
    # This class uses a cheaper text-only OpenAI model for orchestration while
    # we debug tools, trace, and session state without paying realtime-audio costs.
    #
    # LATER:
    # The same backend concepts (tool calls, traces, session state) can be reused
    # once the frontend switches to OpenAI Realtime over WebRTC.
    def __init__(self) -> None:
        self.model = os.environ.get("OPENAI_TEXT_MODEL", "gpt-4.1-mini")

    def _is_first_turn(self, session: SessionState) -> bool:
        return len(session.conversation_history) == 0

    def _looks_like_conversation_end(self, text: str) -> bool:
        lowered = text.lower()
        closing_phrases = [
            "thank you",
            "thanks",
            "that's all",
            "thats all",
            "that's it",
            "thats it",
            "no that's it",
            "no thats it",
            "bye",
            "goodbye",
            "see you",
            "no that's all",
            "no thats all",
            "nothing else",
            "all good",
        ]
        return any(phrase in lowered for phrase in closing_phrases)

    def _looks_like_bare_full_name(self, text: str) -> bool:
        cleaned = text.strip()
        if not cleaned or any(ch.isdigit() for ch in cleaned):
            return False
        if len(cleaned.split()) < 2 or len(cleaned.split()) > 4:
            return False
        return all(re.fullmatch(r"[A-Za-z][A-Za-z\-']*", part) for part in cleaned.split())

    def _explicitly_mentions_phone_verification(self, text: str) -> bool:
        lowered = text.lower()
        triggers = [
            "last 4",
            "last four",
            "phone",
            "digits",
            "digit",
            "verification",
        ]
        return any(trigger in lowered for trigger in triggers)

    def _in_verification_context(self, session: SessionState) -> bool:
        return session.last_policy_code in {
            "need_full_name",
            "need_phone_last4",
            "verification_required",
            "verification_failed",
            "customer_not_found",
        }
#list of tools the llm can use to answer the user's question
    def _supported_capabilities(self) -> Dict[str, str]:
        return {
            "identify_customer": (
                "Finds possible customer matches by name. It can return multiple people with the same first name."
            ),
            "verify_customer": (
                "Verifies a specific customer using the last 4 digits of their phone number."
            ),
            "lookup_ticket": (
                "Looks up a ticket by ID and returns only the ticket ID, status, and priority. "
                "It requires identity verification and does not provide refund process details, ETA, or internal workflow steps."
            ),
            "get_order_status": (
                "Looks up an order by ID and returns only the current order status. It requires identity verification."
            ),
            "schedule_callback": (
                "Schedules a callback from a real human support agent."
            ),
        }

    def _allowed_next_steps(self, intent: str, tool_result: Optional[Dict[str, Any]]) -> list[str]:
        if intent == "lookup_ticket" and tool_result:
            return ["Offer to schedule a callback if the user wants human follow-up."]
        if intent == "order_status" and tool_result:
            return ["Offer to schedule a callback if the user needs additional help."]
        if intent == "schedule_callback" and tool_result:
            return ["Confirm the callback time and close politely or ask if anything else is needed."]
        return ["Ask for a ticket ID, order ID, or preferred callback time only if needed."]

    def _extract_ticket_id(self, text: str) -> Optional[str]:
        match = re.search(r"(ticket|case)\s*(\d+)", text, flags=re.IGNORECASE)
        if match:
            return match.group(2)
        return None

    def _extract_order_id(self, text: str) -> Optional[str]:
        match = re.search(r"order\s*(\d+)", text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _extract_phone_last4(self, text: str) -> Optional[str]:
        match = re.search(r"\b(\d{4})\b", text)
        if match:
            return match.group(1)
        return None

    def _extract_full_name(self, text: str) -> Optional[str]:
        patterns = [
            r"\bmy full name is\s+([A-Za-z][A-Za-z\-']+(?:\s+[A-Za-z][A-Za-z\-']+){1,3})\b",
            r"\bthis is\s+([A-Za-z][A-Za-z\-']+(?:\s+[A-Za-z][A-Za-z\-']+){1,3})\b",
            r"\bi am\s+([A-Za-z][A-Za-z\-']+(?:\s+[A-Za-z][A-Za-z\-']+){1,3})\b",
            r"\bmy name is\s+([A-Za-z][A-Za-z\-']+(?:\s+[A-Za-z][A-Za-z\-']+){1,3})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _normalize_intent(self, raw_intent: str | None, session: SessionState) -> str:
        allowed = {"lookup_ticket", "order_status", "schedule_callback", "general_query"}
        # During a protected verification flow, keep the user's pending goal stable
        # instead of letting the planner drift between lookup/order/verify intents.
        if session.pending_intent in allowed and not session.verified:
            return str(session.pending_intent)
        if raw_intent in allowed:
            return str(raw_intent)
        if session.pending_intent in allowed:
            return str(session.pending_intent)
        return "general_query"

    def _is_sensitive_intent(self, intent: str) -> bool:
        return intent in {"lookup_ticket", "order_status"}

    def _has_new_verification_info(
        self,
        *,
        session: SessionState,
        previous_claimed_name: Optional[str],
        previous_phone_last4: Optional[str],
    ) -> bool:
        return (
            session.claimed_name != previous_claimed_name
            or session.phone_last4 != previous_phone_last4
        )

    def _looks_like_unsupported_detail_request(self, text: str) -> bool:
        lowered = text.lower()
        phrases = [
            "refund process",
            "refund timeline",
            "eta",
            "estimated completion",
            "internal steps",
            "next steps",
            "process details",
        ]
        return any(phrase in lowered for phrase in phrases)

    def _emit_progress(self, *, trace: TraceStore, session_id: str, message: str) -> None:
        trace.emit(
            TraceEvent(
                session_id=session_id,
                type="assistant_progress",
                message=message,
            )
        )

    def _apply_plan_to_session(self, *, session: SessionState, plan: Dict[str, Any], text: str) -> None:
        extracted_full_name = self._extract_full_name(text)
        if not extracted_full_name and session.last_policy_code == "need_full_name" and self._looks_like_bare_full_name(text):
            extracted_full_name = text.strip()

        tool_args = plan.get("tool_args") or {}
        tool_arg_full_name = tool_args.get("full_name") or tool_args.get("name")
        if (
            not extracted_full_name
            and isinstance(tool_arg_full_name, str)
            and len(tool_arg_full_name.strip().split()) >= 2
        ):
            extracted_full_name = tool_arg_full_name.strip()

        if extracted_full_name:
            session.claimed_name = extracted_full_name
        elif plan.get("user_name"):
            proposed_name = str(plan["user_name"]).strip()
            # Preserve the most specific identity the caller has already provided.
            if not session.claimed_name or len(proposed_name.split()) > len(session.claimed_name.split()):
                session.claimed_name = proposed_name
            if not session.user_name:
                session.user_name = session.claimed_name

        explicit_phone_context = self._explicitly_mentions_phone_verification(text)
        verification_context = self._in_verification_context(session)
        bare_four_digit = self._extract_phone_last4(text)

        if not plan.get("phone_last4"):
            extracted_phone_last4 = bare_four_digit
            if extracted_phone_last4 and (explicit_phone_context or verification_context):
                plan["phone_last4"] = extracted_phone_last4
        if plan.get("phone_last4") and (explicit_phone_context or verification_context):
            session.phone_last4 = str(plan["phone_last4"])

        if plan.get("ticket_id"):
            session.ticket_id = str(plan["ticket_id"])
        elif (
            session.verified
            and bare_four_digit
            and not explicit_phone_context
            and (
                session.last_policy_code in {"verified_ready", "need_ticket_id"}
                or plan.get("intent") == "lookup_ticket"
            )
        ):
            session.ticket_id = bare_four_digit
        elif session.last_policy_code == "need_ticket_id":
            extracted_ticket_id = bare_four_digit
            if extracted_ticket_id and not explicit_phone_context:
                session.ticket_id = extracted_ticket_id
        elif not session.ticket_id:
            extracted_ticket_id = self._extract_ticket_id(text)
            if extracted_ticket_id:
                session.ticket_id = extracted_ticket_id

        if plan.get("order_id"):
            session.order_id = str(plan["order_id"])
        elif (
            session.verified
            and bare_four_digit
            and not explicit_phone_context
            and (
                session.last_policy_code in {"verified_ready", "need_order_id"}
                or plan.get("intent") == "order_status"
            )
        ):
            session.order_id = bare_four_digit
        elif session.last_policy_code == "need_order_id":
            extracted_order_id = bare_four_digit
            if extracted_order_id and not explicit_phone_context:
                session.order_id = extracted_order_id
        elif not session.order_id:
            extracted_order_id = self._extract_order_id(text)
            if extracted_order_id:
                session.order_id = extracted_order_id

    def handle_text(self, *, session: SessionState, text: str, trace: TraceStore) -> str:
        prior_claimed_name = session.claimed_name
        prior_phone_last4 = session.phone_last4

        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="user_input",
                message="User input received",
                data={"text": text},
            )
        )

        plan = self._plan(session=session, text=text)
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="llm_plan",
                message="OpenAI planned the next action",
                data=plan,
            )
        )

        self._apply_plan_to_session(session=session, plan=plan, text=text)

        if session.claimed_name and not session.user_name:
            session.user_name = session.claimed_name

        if session.claimed_name and session.claimed_name != prior_claimed_name:
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="user_identified",
                    message=f"Caller identified as: {session.claimed_name}",
                    data={"user_name": session.claimed_name},
                )
            )

        sentiment = str(plan.get("sentiment") or "neutral")
        session.sentiment = sentiment
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="sentiment",
                message=f"Sentiment: {sentiment}",
                data={"sentiment": sentiment},
            )
        )

        intent = self._normalize_intent(plan.get("intent"), session)
        if self._is_sensitive_intent(intent):
            session.pending_intent = intent
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="intent",
                message=f"Intent detected: {intent}",
                data={"intent": intent},
            )
        )

        policy_outcome = self._execute_policy(
            session=session,
            trace=trace,
            plan=plan,
            intent=intent,
            user_text=text,
            has_new_verification_info=self._has_new_verification_info(
                session=session,
                previous_claimed_name=prior_claimed_name,
                previous_phone_last4=prior_phone_last4,
            ),
        )
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="policy_outcome",
                message=f"Policy outcome: {policy_outcome['code']}",
                data=policy_outcome,
            )
        )
        session.last_policy_code = policy_outcome["code"]

        response_text = self._compose_response(
            session=session,
            user_text=text,
            intent=intent,
            policy_outcome=policy_outcome,
        )

        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="response",
                message="Response generated",
                data={"text": response_text},
            )
        )

        session.conversation_history.append({"role": "user", "text": text})
        session.conversation_history.append({"role": "assistant", "text": response_text})
        return response_text

    def _plan(self, *, session: SessionState, text: str) -> Dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are planning the next backend action for a customer support assistant. "
                    "Return JSON only. "
                    "Choose exactly one intent from: lookup_ticket, order_status, "
                    "schedule_callback, general_query. "
                    "Choose sentiment from: positive, neutral, negative. "
                    "Default to neutral unless the user clearly expresses satisfaction/gratitude "
                    "or frustration/anger. A simple greeting or request is neutral. "
                    "If no tool is needed, set tool_name to null and tool_args to {}. "
                    "If the user clearly says their name, extract it into user_name. "
                    "If the user shares a ticket ID, order ID, customer name, or phone last 4, extract them. "
                    "Use only these tool names: identify_customer, verify_customer, lookup_ticket, "
                    "get_order_status, schedule_callback. "
                    "Do not call a tool unless the current request can actually be answered by that tool. "
                    "Ticket lookups and order lookups require identity verification first. "
                    "If identity is not verified yet, prefer identify_customer or verify_customer before sensitive lookups. "
                    "If the user asks for refund process details, ETA, internal next steps, or other details "
                    "that are not available from the listed tools, choose general_query and do not force a tool call."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "session_state": {
                            "user_name": session.user_name,
                            "claimed_name": session.claimed_name,
                            "customer_id": session.customer_id,
                            "customer_full_name": session.customer_full_name,
                            "verified": session.verified,
                            "candidate_customer_ids": session.candidate_customer_ids,
                            "ticket_id": session.ticket_id,
                            "order_id": session.order_id,
                            "sentiment": session.sentiment,
                            "conversation_history": session.conversation_history[-6:],
                        },
                        "available_tools": self._supported_capabilities(),
                        "user_text": text,
                        "return_json_schema": {
                            "intent": "lookup_ticket | order_status | schedule_callback | general_query",
                            "sentiment": "positive | neutral | negative",
                            "user_name": "string or null",
                            "customer_id": "string or null",
                            "ticket_id": "string or null",
                            "order_id": "string or null",
                            "phone_last4": "string or null",
                            "tool_name": "identify_customer | verify_customer | lookup_ticket | get_order_status | schedule_callback | null",
                            "tool_args": "object",
                            "reasoning_summary": "short string",
                        },
                    }
                ),
            },
        ]

        try:
            response = _client().chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=messages,
                temperature=0,
            )
        except Exception as exc:
            raise TextAgentConfigError(f"OpenAI planning call failed: {exc}") from exc

        content = response.choices[0].message.content or "{}"
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise TextAgentConfigError(f"OpenAI returned invalid planning JSON: {content}") from exc

    def _run_tool(
        self,
        *,
        session: SessionState,
        trace: TraceStore,
        plan: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        tool_name = plan.get("tool_name")
        tool_args = plan.get("tool_args") or {}

        if not tool_name:
            return None

        if tool_name == "identify_customer":
            name = str(tool_args.get("name") or plan.get("user_name") or session.claimed_name or "").strip()
            if not name:
                return None
            self._emit_progress(
                trace=trace,
                session_id=session.session_id,
                message="Let me find the right customer profile for you first.",
            )
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_call",
                    message=f"Tool called: identify_customer({name})",
                    data={"tool": "identify_customer", "args": {"name": name}},
                )
            )
            raw_result = identify_customer(name)
            session.candidate_customer_ids = [
                match["customer_id"] for match in raw_result.get("matches", [])
            ]
            if raw_result["status"] == "single_match":
                session.customer_id = raw_result["matches"][0]["customer_id"]
                session.customer_full_name = raw_result["matches"][0]["full_name"]
            result = {"tool": "identify_customer", **raw_result}

        elif tool_name == "verify_customer":
            customer_id = str(
                tool_args.get("customer_id")
                or plan.get("customer_id")
                or session.customer_id
                or (session.candidate_customer_ids[0] if len(session.candidate_customer_ids) == 1 else "")
            ).strip()
            phone_last4 = str(
                tool_args.get("phone_last4")
                or plan.get("phone_last4")
                or ""
            ).strip()
            if not phone_last4:
                phone_last4 = self._extract_phone_last4(plan.get("reasoning_summary", "") or "") or ""
            if not customer_id:
                return None
            self._emit_progress(
                trace=trace,
                session_id=session.session_id,
                message="I just need to verify your identity before I access that record.",
            )
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_call",
                    message=f"Tool called: verify_customer({customer_id})",
                    data={"tool": "verify_customer", "args": {"customer_id": customer_id, "phone_last4": phone_last4}},
                )
            )
            raw_result = verify_customer(customer_id, phone_last4)
            if raw_result.get("status") == "verified":
                session.verified = True
                session.customer_id = customer_id
                session.customer_full_name = raw_result["full_name"]
                session.user_name = raw_result["full_name"]
                session.verification_method = "phone_last4"
            result = {"tool": "verify_customer", **raw_result}

            # If the caller had already asked about a specific record, resume that request once verified.
            if result.get("status") == "verified" and session.ticket_id:
                self._emit_progress(
                    trace=trace,
                    session_id=session.session_id,
                    message="Thanks, you are verified. Just getting the latest ticket update for you.",
                )
                ticket_result = lookup_ticket(
                    session.ticket_id,
                    customer_id=session.customer_id,
                    verified=session.verified,
                )
                trace.emit(
                    TraceEvent(
                        session_id=session.session_id,
                        type="tool_call",
                        message=f"Tool called: lookup_ticket({session.ticket_id})",
                        data={"tool": "lookup_ticket", "args": {"case_id": session.ticket_id}},
                    )
                )
                trace.emit(
                    TraceEvent(
                        session_id=session.session_id,
                        type="tool_result",
                        message="Tool result received",
                        data={"result": ticket_result},
                    )
                )
                result = {
                    "tool": "verify_customer",
                    "status": "verified_and_lookup_complete",
                    "verification": raw_result,
                    "follow_up_tool": "lookup_ticket",
                    "follow_up_result": ticket_result,
                }
            elif result.get("status") == "verified" and session.order_id:
                self._emit_progress(
                    trace=trace,
                    session_id=session.session_id,
                    message="Thanks, you are verified. Just getting the latest order update for you.",
                )
                order_result = get_order_status(
                    session.order_id,
                    customer_id=session.customer_id,
                    verified=session.verified,
                )
                trace.emit(
                    TraceEvent(
                        session_id=session.session_id,
                        type="tool_call",
                        message=f"Tool called: get_order_status({session.order_id})",
                        data={"tool": "get_order_status", "args": {"order_id": session.order_id}},
                    )
                )
                trace.emit(
                    TraceEvent(
                        session_id=session.session_id,
                        type="tool_result",
                        message="Tool result received",
                        data={"result": order_result},
                    )
                )
                result = {
                    "tool": "verify_customer",
                    "status": "verified_and_lookup_complete",
                    "verification": raw_result,
                    "follow_up_tool": "get_order_status",
                    "follow_up_result": order_result,
                }

        elif tool_name == "lookup_ticket":
            case_id = str(tool_args.get("case_id") or tool_args.get("ticket_id") or "").strip()
            if not case_id:
                return None
            session.ticket_id = case_id
            self._emit_progress(
                trace=trace,
                session_id=session.session_id,
                message="Just getting the latest ticket update for you.",
            )
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_call",
                    message=f"Tool called: lookup_ticket({case_id})",
                    data={"tool": "lookup_ticket", "args": {"case_id": case_id}},
                )
            )
            raw_result = lookup_ticket(
                case_id,
                customer_id=session.customer_id,
                verified=session.verified,
            )
            result = {"tool": "lookup_ticket", **raw_result}

        elif tool_name == "get_order_status":
            order_id = str(tool_args.get("order_id") or "").strip()
            if not order_id:
                return None
            session.order_id = order_id
            self._emit_progress(
                trace=trace,
                session_id=session.session_id,
                message="Just getting the latest order update for you.",
            )
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_call",
                    message=f"Tool called: get_order_status({order_id})",
                    data={"tool": "get_order_status", "args": {"order_id": order_id}},
                )
            )
            raw_result = get_order_status(
                order_id,
                customer_id=session.customer_id,
                verified=session.verified,
            )
            result = {"tool": "get_order_status", **raw_result}

        elif tool_name == "schedule_callback":
            when = str(tool_args.get("time") or tool_args.get("when") or "next available time").strip()
            reason = str(tool_args.get("reason") or "general support").strip()
            self._emit_progress(
                trace=trace,
                session_id=session.session_id,
                message="Let me arrange a callback with a human support agent.",
            )
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_call",
                    message=f"Tool called: schedule_callback({when})",
                    data={"tool": "schedule_callback", "args": {"when": when, "reason": reason}},
                )
            )
            raw_result = schedule_callback(
                when,
                customer_id=session.customer_id,
                customer_name=session.customer_full_name or session.claimed_name or session.user_name,
                reason=reason,
            )
            result = {"tool": "schedule_callback", **raw_result}

        else:
            return None

        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="tool_result",
                message="Tool result received",
                data={"result": result},
            )
        )
        return result

    def _build_policy_outcome(
        self,
        *,
        code: str,
        tool_result: Optional[Dict[str, Any]] = None,
        safe_facts: Optional[Dict[str, Any]] = None,
        allowed_next_steps: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "code": code,
            "tool_result": tool_result,
            "safe_facts": safe_facts or {},
            "allowed_next_steps": allowed_next_steps or [],
        }

    def _verify_and_resume_pending_lookup(
        self,
        *,
        session: SessionState,
        trace: TraceStore,
        customer_id: str,
        phone_last4: str,
    ) -> Dict[str, Any]:
        self._emit_progress(
            trace=trace,
            session_id=session.session_id,
            message="I just need to verify your identity before I access that record.",
        )
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="tool_call",
                message=f"Tool called: verify_customer({customer_id})",
                data={"tool": "verify_customer", "args": {"customer_id": customer_id, "phone_last4": phone_last4}},
            )
        )
        verification = verify_customer(customer_id, phone_last4)
        trace.emit(
            TraceEvent(
                session_id=session.session_id,
                type="tool_result",
                message="Tool result received",
                data={"result": {"tool": "verify_customer", **verification}},
            )
        )

        if verification.get("status") != "verified":
            session.last_verification_outcome = "verification_failed"
            session.last_failed_name = session.claimed_name
            session.last_failed_phone_last4 = phone_last4
            return self._build_policy_outcome(
                code="verification_failed",
                tool_result={"tool": "verify_customer", **verification},
                safe_facts={"claimed_name": session.claimed_name, "phone_last4": phone_last4},
                allowed_next_steps=[
                    "Ask for the correct full name and the last 4 digits again, or offer a callback with a human support agent.",
                ],
            )

        session.verified = True
        session.customer_id = customer_id
        session.customer_full_name = verification["full_name"]
        session.user_name = verification["full_name"]
        session.verification_method = "phone_last4"
        session.last_verification_outcome = "verified"
        session.last_failed_name = None
        session.last_failed_phone_last4 = None

        pending_intent = session.pending_intent
        if pending_intent == "lookup_ticket" and session.ticket_id:
            self._emit_progress(
                trace=trace,
                session_id=session.session_id,
                message="Thanks, you are verified. Just getting the latest ticket update for you.",
            )
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_call",
                    message=f"Tool called: lookup_ticket({session.ticket_id})",
                    data={"tool": "lookup_ticket", "args": {"case_id": session.ticket_id}},
                )
            )
            lookup_result = lookup_ticket(
                session.ticket_id,
                customer_id=session.customer_id,
                verified=True,
            )
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_result",
                    message="Tool result received",
                    data={"result": lookup_result},
                )
            )
            return self._policy_outcome_from_tool_result(
                session=session,
                intent=pending_intent,
                tool_result={
                    "tool": "lookup_ticket",
                    **lookup_result,
                },
            )

        if pending_intent == "order_status" and session.order_id:
            self._emit_progress(
                trace=trace,
                session_id=session.session_id,
                message="Thanks, you are verified. Just getting the latest order update for you.",
            )
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_call",
                    message=f"Tool called: get_order_status({session.order_id})",
                    data={"tool": "get_order_status", "args": {"order_id": session.order_id}},
                )
            )
            order_result = get_order_status(
                session.order_id,
                customer_id=session.customer_id,
                verified=True,
            )
            trace.emit(
                TraceEvent(
                    session_id=session.session_id,
                    type="tool_result",
                    message="Tool result received",
                    data={"result": order_result},
                )
            )
            return self._policy_outcome_from_tool_result(
                session=session,
                intent=pending_intent,
                tool_result={
                    "tool": "get_order_status",
                    **order_result,
                },
            )

        return self._build_policy_outcome(
            code="verified_ready",
            tool_result={"tool": "verify_customer", **verification},
            safe_facts={"customer_full_name": session.customer_full_name},
            allowed_next_steps=[
                "Ask for the ticket ID or order ID if it is still needed, or offer a callback.",
            ],
        )

    def _handle_sensitive_intent_context(
        self,
        *,
        session: SessionState,
        trace: TraceStore,
        intent: str,
        user_text: str,
        has_new_verification_info: bool,
    ) -> Dict[str, Any]:
        pending_intent = session.pending_intent or intent
        session.pending_intent = pending_intent

        if (
            session.last_verification_outcome == "customer_not_found"
            and not has_new_verification_info
        ):
            return self._build_policy_outcome(
                code="customer_not_found",
                safe_facts={
                    "claimed_name": session.last_failed_name or session.claimed_name,
                    "phone_last4": session.last_failed_phone_last4 or session.phone_last4,
                    "repeated": True,
                },
                allowed_next_steps=[
                    "Explain that no matching customer was found with the provided details and offer a callback with a human support agent.",
                ],
            )

        if session.verified:
            if pending_intent == "lookup_ticket" and not session.ticket_id:
                return self._build_policy_outcome(
                    code="need_ticket_id",
                    safe_facts={},
                    allowed_next_steps=["Ask for the ticket ID."],
                )
            if pending_intent == "order_status" and not session.order_id:
                return self._build_policy_outcome(
                    code="need_order_id",
                    safe_facts={},
                    allowed_next_steps=["Ask for the order ID."],
                )
            return self._run_verified_lookup(session=session, trace=trace, intent=pending_intent)

        # If multiple possible customers exist, resolve identity using whatever details
        # have been gathered so far rather than asking the same question again.
        if session.candidate_customer_ids:
            resolution = resolve_customer_identity(
                name_query=session.claimed_name,
                phone_last4=session.phone_last4,
                candidate_customer_ids=session.candidate_customer_ids,
            )
            if resolution["status"] == "single_match":
                match = resolution["matches"][0]
                session.customer_id = match["customer_id"]
                session.customer_full_name = match["full_name"]
                if session.phone_last4:
                    return self._verify_and_resume_pending_lookup(
                        session=session,
                        trace=trace,
                        customer_id=session.customer_id,
                        phone_last4=session.phone_last4,
                    )
                return self._build_policy_outcome(
                    code="need_phone_last4",
                    safe_facts={"customer_full_name": session.customer_full_name},
                    allowed_next_steps=["Ask for the last 4 digits of the phone number."],
                )

            if resolution["status"] == "multiple_matches":
                if not session.claimed_name or len(session.claimed_name.split()) < 2:
                    return self._build_policy_outcome(
                        code="need_full_name",
                        safe_facts={"matches": resolution["matches"]},
                        allowed_next_steps=["Ask for the full name to narrow down the customer record."],
                    )
                if not session.phone_last4:
                    return self._build_policy_outcome(
                        code="need_phone_last4",
                        safe_facts={"matches": resolution["matches"]},
                        allowed_next_steps=["Ask for the last 4 digits of the phone number."],
                    )

            if resolution["status"] == "no_match":
                session.last_verification_outcome = "customer_not_found"
                session.last_failed_name = session.claimed_name
                session.last_failed_phone_last4 = session.phone_last4
                return self._build_policy_outcome(
                    code="customer_not_found",
                    safe_facts={
                        "claimed_name": session.claimed_name,
                        "phone_last4": session.phone_last4,
                        "repeated": False,
                    },
                    allowed_next_steps=[
                        "Explain that no matching customer was found and offer a callback with a human support agent.",
                    ],
                )

        if session.claimed_name:
            identify_result = self._run_tool(
                session=session,
                trace=trace,
                plan={"tool_name": "identify_customer", "tool_args": {"name": session.claimed_name}},
            )
            if identify_result:
                return self._policy_outcome_from_tool_result(
                    session=session,
                    intent=pending_intent,
                    tool_result=identify_result,
                )

        return self._build_policy_outcome(
            code="verification_required",
            safe_facts={
                "claimed_name": session.claimed_name,
                "phone_last4": session.phone_last4,
                "ticket_id": session.ticket_id,
                "order_id": session.order_id,
            },
            allowed_next_steps=[
                "Ask for the missing full name and/or last 4 digits of the phone number for verification.",
            ],
        )

    def _run_verified_lookup(
        self,
        *,
        session: SessionState,
        trace: TraceStore,
        intent: str,
    ) -> Dict[str, Any]:
        if intent == "lookup_ticket" and session.ticket_id:
            tool_result = self._run_tool(
                session=session,
                trace=trace,
                plan={"tool_name": "lookup_ticket", "tool_args": {"ticket_id": session.ticket_id}},
            )
            if tool_result:
                return self._policy_outcome_from_tool_result(
                    session=session,
                    intent=intent,
                    tool_result=tool_result,
                )
        if intent == "order_status" and session.order_id:
            tool_result = self._run_tool(
                session=session,
                trace=trace,
                plan={"tool_name": "get_order_status", "tool_args": {"order_id": session.order_id}},
            )
            if tool_result:
                return self._policy_outcome_from_tool_result(
                    session=session,
                    intent=intent,
                    tool_result=tool_result,
                )
        return self._build_policy_outcome(
            code="general_reply",
            safe_facts={},
            allowed_next_steps=self._allowed_next_steps(intent, None),
        )

    def _execute_policy(
        self,
        *,
        session: SessionState,
        trace: TraceStore,
        plan: Dict[str, Any],
        intent: str,
        user_text: str,
        has_new_verification_info: bool,
    ) -> Dict[str, Any]:
        if self._looks_like_conversation_end(user_text):
            return {
                "code": "closing",
                "tool_result": None,
                "safe_facts": {},
                "allowed_next_steps": [],
            }

        if self._looks_like_unsupported_detail_request(user_text):
            return self._build_policy_outcome(
                code="unsupported_capability",
                safe_facts={
                    "message": "The backend can confirm ticket or order status, but it cannot provide internal refund process details, ETA, or internal workflow steps.",
                },
                allowed_next_steps=[
                    "Offer to schedule a callback with a human support agent.",
                ],
            )

        if self._is_sensitive_intent(intent) or (
            session.pending_intent and self._is_sensitive_intent(session.pending_intent)
        ):
            return self._handle_sensitive_intent_context(
                session=session,
                trace=trace,
                intent=intent,
                user_text=user_text,
                has_new_verification_info=has_new_verification_info,
            )

        tool_result = self._run_tool(session=session, trace=trace, plan=plan)
        if tool_result is None:
            return self._build_policy_outcome(
                code="general_reply",
                safe_facts={},
                allowed_next_steps=self._allowed_next_steps(intent, None),
            )

        return self._policy_outcome_from_tool_result(
            session=session,
            intent=intent,
            tool_result=tool_result,
        )

    def _policy_outcome_from_tool_result(
        self,
        *,
        session: SessionState,
        intent: str,
        tool_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        tool = tool_result.get("tool")
        status = tool_result.get("status")

        if tool == "identify_customer":
            status_code = f"identify_customer_{status}"
            if status == "multiple_matches" and (
                not session.claimed_name or len(session.claimed_name.split()) < 2
            ):
                status_code = "need_full_name"
            elif status == "single_match" and not session.phone_last4:
                status_code = "need_phone_last4"
            elif status == "no_match":
                status_code = "customer_not_found"
            return {
                "code": status_code,
                "tool_result": tool_result,
                "safe_facts": {
                    "matches": tool_result.get("matches", []),
                    "claimed_name": session.claimed_name,
                },
                "allowed_next_steps": [
                    "Ask for full name if no match was found.",
                    "Ask for the last 4 digits of the phone number to verify identity.",
                ],
            }

        if tool == "verify_customer":
            safe_facts: Dict[str, Any] = {
                "customer_full_name": session.customer_full_name,
            }
            if status == "verified_and_lookup_complete":
                safe_facts["follow_up_tool"] = tool_result.get("follow_up_tool")
                safe_facts["follow_up_result"] = tool_result.get("follow_up_result")
            return {
                "code": f"verify_customer_{status}",
                "tool_result": tool_result,
                "safe_facts": safe_facts,
                "allowed_next_steps": self._allowed_next_steps(
                    intent,
                    tool_result.get("follow_up_result") if status == "verified_and_lookup_complete" else tool_result,
                ),
            }

        if tool == "lookup_ticket":
            safe_facts = {}
            if status == "success":
                session.pending_intent = None
                safe_facts["record"] = tool_result.get("record", {})
            return {
                "code": f"lookup_ticket_{status}",
                "tool_result": tool_result,
                "safe_facts": safe_facts,
                "allowed_next_steps": self._allowed_next_steps(intent, tool_result),
            }

        if tool == "get_order_status":
            safe_facts = {}
            if status == "success":
                session.pending_intent = None
                safe_facts["record"] = tool_result.get("record", {})
            return {
                "code": f"get_order_status_{status}",
                "tool_result": tool_result,
                "safe_facts": safe_facts,
                "allowed_next_steps": self._allowed_next_steps(intent, tool_result),
            }

        if tool == "schedule_callback":
            if status == "success":
                session.pending_intent = None
            return {
                "code": f"schedule_callback_{status}",
                "tool_result": tool_result,
                "safe_facts": {
                    "time": tool_result.get("time"),
                    "queue": tool_result.get("queue"),
                    "reason": tool_result.get("reason"),
                },
                "allowed_next_steps": [],
            }

        return {
            "code": "general_reply",
            "tool_result": tool_result,
            "safe_facts": {},
            "allowed_next_steps": self._allowed_next_steps(intent, tool_result),
        }

    def _compose_response(
        self,
        *,
        session: SessionState,
        user_text: str,
        intent: str,
        policy_outcome: Dict[str, Any],
    ) -> str:
        is_first_turn = self._is_first_turn(session)
        likely_closing_turn = self._looks_like_conversation_end(user_text)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are the response composer for a customer support assistant. "
                    "Use only the provided policy_outcome, safe_facts, and allowed_next_steps. "
                    "Do not invent ticket details, refund process details, ETA, internal workflow steps, or unsupported capabilities. "
                    "If verification is required, ask for the exact missing verification details. "
                    "If ownership mismatch or verification failure occurs, do not reveal protected information. "
                    "If policy_outcome.code is need_full_name, ask for the full name only. "
                    "If policy_outcome.code is need_phone_last4, ask for the last 4 digits only. "
                    "If policy_outcome.code is customer_not_found, clearly say no matching customer was found. "
                    "If policy_outcome.safe_facts.repeated is true, stay firm and do not ask for the same full name again unless the user provides new identifying information. "
                    "If policy_outcome.code is verification_failed, clearly say the provided verification details did not match a customer record. "
                    "If policy_outcome.code is need_order_id or need_ticket_id, ask only for that missing ID. "
                    "If this is the first turn, start with a warm welcome. "
                    "If the user is ending the conversation, close warmly and politely. "
                    "Keep the reply natural and concise in 1-3 short sentences."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "user_text": user_text,
                        "intent": intent,
                        "policy_outcome": policy_outcome,
                        "session_state": {
                            "claimed_name": session.claimed_name,
                            "customer_full_name": session.customer_full_name,
                            "verified": session.verified,
                            "ticket_id": session.ticket_id,
                            "order_id": session.order_id,
                            "sentiment": session.sentiment,
                            "is_first_turn": is_first_turn,
                            "likely_closing_turn": likely_closing_turn,
                        },
                    }
                ),
            },
        ]

        try:
            response = _client().chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
            )
        except Exception as exc:
            raise TextAgentConfigError(f"OpenAI response generation failed: {exc}") from exc

        text = response.choices[0].message.content or ""
        cleaned = text.strip()
        if not cleaned:
            raise TextAgentConfigError("OpenAI returned an empty assistant response")
        return cleaned

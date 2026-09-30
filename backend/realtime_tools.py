from __future__ import annotations

import re
import time
from typing import Any, Dict

from backend.config import get_response_mode
from backend.conversation import SessionState
from backend.speech_directives import DIRECTIVES
from backend.tool_contract import PURPOSES, TOOL_NAMES
from backend.tools import (
    get_order_status,
    identify_customer,
    lookup_ticket,
    resolve_customer_identity,
    schedule_callback,
)


class RealtimeToolError(ValueError):
    """Raised when a realtime tool call does not satisfy the public contract."""


class RealtimeToolRouter:
    def execute(
        self,
        *,
        session: SessionState,
        tool_name: str,
        tool_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        started_at = time.perf_counter()
        if tool_name not in TOOL_NAMES:
            raise RealtimeToolError(f"Unsupported realtime tool: {tool_name}")

        (
            purpose,
            caller_name,
            caller_phone,
            order_id,
            ticket_id,
            callback_time,
            attempt,
        ) = self._validate_args(tool_args)
        session.pending_intent = purpose
        if order_id:
            session.order_id = order_id
        if ticket_id:
            session.ticket_id = ticket_id
        if callback_time:
            session.callback_time = callback_time

        if tool_name == "customer_identity":
            action, key, information = self._identity(
                session=session,
                purpose=purpose,
                caller_name=caller_name,
                caller_phone=caller_phone,
                attempt=attempt,
            )
        elif tool_name == "customer_lookup":
            action, key, information = self._lookup(session=session, purpose=purpose)
        else:
            action, key, information = self._callback(
                session=session,
                purpose=purpose,
                callback_time=callback_time,
            )

        response_mode = get_response_mode()
        directive = None
        if key is not None:
            directive_text = DIRECTIVES[key].format(**information)
            directive = {
                "key": key,
                "response_text": directive_text,
                "require_repeat_verbatim": response_mode == "speech_directive",
            }
        return {
            "purpose": purpose,
            "action": action,
            "caller_name": caller_name or None,
            "caller_phone": caller_phone or None,
            "order_id": session.order_id or None,
            "ticket_id": session.ticket_id or None,
            "callback_time": session.callback_time or None,
            "attempt": attempt,
            "information": information,
            "session_state": self._session_snapshot(session),
            "response_mode": response_mode,
            "directive": directive,
            "backend_tool_ms": round((time.perf_counter() - started_at) * 1000, 1),
        }

    def _validate_args(
        self,
        arguments: Dict[str, Any],
    ) -> tuple[str, str, str, str, str, str, int]:
        if not isinstance(arguments, dict):
            raise RealtimeToolError("Tool arguments must be an object")

        purpose = arguments.get("purpose")
        if purpose not in PURPOSES:
            raise RealtimeToolError(f"Unsupported purpose: {purpose}")

        attempt = arguments.get("attempt")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
            raise RealtimeToolError("attempt must be a non-negative integer")

        caller_name = self._clean_optional(arguments.get("caller_name"))
        caller_phone = self._clean_optional(arguments.get("caller_phone"))
        order_id = self._clean_optional(arguments.get("order_id"))
        ticket_id = self._clean_optional(arguments.get("ticket_id"))
        callback_time = self._clean_optional(arguments.get("callback_time"))
        return purpose, caller_name, caller_phone, order_id, ticket_id, callback_time, attempt

    @staticmethod
    def _clean_optional(value: Any) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise RealtimeToolError("Optional tool fields must be strings or null")
        return value.strip()

    def _identity(
        self,
        *,
        session: SessionState,
        purpose: str,
        caller_name: str,
        caller_phone: str,
        attempt: int,
    ) -> tuple[str, str | None, Dict[str, Any]]:
        if not caller_name:
            return self._request_or_repair(
                session=session,
                field_name="caller_name",
                action="identify_caller",
                initial_key=f"customer_identity.{purpose}.no_caller_name",
                repair_key=f"customer_identity.{purpose}.repeat_caller_name",
                supplied_attempt=attempt,
            )

        session.claimed_name = caller_name
        session.user_name = caller_name
        identified = identify_customer(caller_name)
        matches = identified.get("matches") or []
        session.candidate_customer_ids = [str(match["customer_id"]) for match in matches]

        if not matches:
            return (
                "route_to_callback",
                f"customer_identity.{purpose}.no_name_match",
                {"match_count": 0},
            )

        requires_phone_verification = bool(identified.get("requires_phone_verification"))
        if len(matches) == 1 and not requires_phone_verification:
            self._accept_identity(session, matches[0], method="unique_name")
            return (
                "ready_for_lookup",
                None,
                {"customer_id": session.customer_id, "customer_name": session.customer_full_name},
            )

        phone_last4 = self._phone_last4(caller_phone)
        if not phone_last4:
            action, key, information = self._request_or_repair(
                session=session,
                field_name="caller_phone",
                action="verify_caller",
                initial_key=f"customer_identity.{purpose}.verify_name",
                repair_key=f"customer_identity.{purpose}.repeat_caller_phone",
                supplied_attempt=0,
            )
            information["match_count"] = len(matches)
            return action, key, information

        resolved = resolve_customer_identity(
            name_query=caller_name,
            phone_last4=phone_last4,
            candidate_customer_ids=session.candidate_customer_ids,
        )
        resolved_matches = resolved.get("matches") or []
        if len(resolved_matches) != 1:
            return (
                "route_to_callback",
                f"customer_identity.{purpose}.verification_failed",
                {"match_count": len(resolved_matches)},
            )

        session.phone_last4 = phone_last4
        self._accept_identity(session, resolved_matches[0], method="name_and_phone")
        return (
            "ready_for_lookup",
            None,
            {"customer_id": session.customer_id, "customer_name": session.customer_full_name},
        )

    def _lookup(self, *, session: SessionState, purpose: str) -> tuple[str, str | None, Dict[str, Any]]:
        if purpose == "customer_support":
            return (
                "route_to_callback",
                "customer_lookup.customer_support.callback_required",
                {},
            )

        if not session.verified or not session.customer_id:
            key = f"customer_lookup.{purpose}.identity_required"
            return "route_to_identity", key, {}

        if purpose == "order_status":
            if not session.order_id:
                return self._request_or_repair(
                    session=session,
                    field_name="order_id",
                    action="request_order_id",
                    initial_key="customer_lookup.order_status.no_order_id",
                    repair_key="customer_lookup.order_status.repeat_order_id",
                    supplied_attempt=0,
                )
            result = get_order_status(
                session.order_id,
                customer_id=session.customer_id,
                verified=session.verified,
            )
            if result.get("status") != "success":
                return (
                    "route_to_callback",
                    "customer_lookup.order_status.not_found",
                    {"order_id": session.order_id},
                )
            record = result["record"]
            return (
                "return_order_status",
                "customer_lookup.order_status.success",
                {"order_id": record["order_id"], "status": record["status"]},
            )

        if not session.ticket_id:
            return self._request_or_repair(
                session=session,
                field_name="ticket_id",
                action="request_ticket_id",
                initial_key="customer_lookup.ticket_status.no_ticket_id",
                repair_key="customer_lookup.ticket_status.repeat_ticket_id",
                supplied_attempt=0,
            )
        result = lookup_ticket(
            session.ticket_id,
            customer_id=session.customer_id,
            verified=session.verified,
        )
        if result.get("status") != "success":
            return (
                "route_to_callback",
                "customer_lookup.ticket_status.not_found",
                {"case_id": session.ticket_id},
            )
        record = result["record"]
        return (
            "return_ticket_status",
            "customer_lookup.ticket_status.success",
            {
                "case_id": record["case_id"],
                "status": record["status"],
                "priority": record["priority"],
            },
        )

    def _callback(
        self,
        *,
        session: SessionState,
        purpose: str,
        callback_time: str,
    ) -> tuple[str, str, Dict[str, Any]]:
        requested_time = callback_time or session.callback_time or ""
        if not requested_time and session.information_attempts.get("callback_time", 0) < 1:
            self._mark_requested(session, "callback_time")
            return (
                "request_callback_time",
                f"support_callback.{purpose}.no_callback_time",
                {"required_field": "callback_time", "next_attempt": 1},
            )

        if not requested_time and session.recognition_repairs.get("callback_time", 0) < 1:
            session.recognition_repairs["callback_time"] = 1
            return (
                "request_callback_time",
                f"support_callback.{purpose}.repeat_callback_time",
                {
                    "required_field": "callback_time",
                    "recognition_repair": True,
                    "next_attempt": 1,
                },
            )

        # If callback timing is still unavailable after one recognition repair,
        # use a deterministic fallback instead of looping.
        if not requested_time:
            requested_time = "the next available time"

        session.callback_time = requested_time
        result = schedule_callback(
            requested_time,
            customer_id=session.customer_id,
            customer_name=session.customer_full_name or session.claimed_name,
            reason=purpose,
        )
        return (
            "schedule_callback",
            f"support_callback.{purpose}.scheduled",
            {"time": result["time"], "queue": result["queue"]},
        )

    @staticmethod
    def _phone_last4(value: str) -> str:
        digits = re.sub(r"\D", "", value)
        return digits[-4:] if len(digits) >= 4 else ""

    @staticmethod
    def _mark_requested(session: SessionState, field_name: str) -> None:
        session.information_attempts[field_name] = session.information_attempts.get(field_name, 0) + 1

    def _request_or_repair(
        self,
        *,
        session: SessionState,
        field_name: str,
        action: str,
        initial_key: str,
        repair_key: str,
        supplied_attempt: int,
    ) -> tuple[str, str | None, Dict[str, Any]]:
        if not self._was_requested(session, field_name, supplied_attempt):
            self._mark_requested(session, field_name)
            return action, initial_key, {"required_field": field_name, "next_attempt": 1}

        if session.recognition_repairs.get(field_name, 0) < 1:
            session.recognition_repairs[field_name] = 1
            return (
                action,
                repair_key,
                {
                    "required_field": field_name,
                    "recognition_repair": True,
                    "next_attempt": 1,
                },
            )

        return (
            "route_to_callback",
            None,
            {"missing_field": field_name, "reason": "unrecognized_after_retry"},
        )

    @staticmethod
    def _was_requested(session: SessionState, field_name: str, supplied_attempt: int) -> bool:
        return max(session.information_attempts.get(field_name, 0), supplied_attempt) >= 1

    @staticmethod
    def _mark_declined(session: SessionState, field_name: str) -> None:
        if field_name not in session.declined_information:
            session.declined_information.append(field_name)

    @staticmethod
    def _accept_identity(session: SessionState, match: Dict[str, Any], *, method: str) -> None:
        session.customer_id = str(match["customer_id"])
        session.customer_full_name = str(match["full_name"])
        session.user_name = session.customer_full_name
        session.verified = True
        session.verification_method = method
        session.last_verification_outcome = "verified"

    @staticmethod
    def _session_snapshot(session: SessionState) -> Dict[str, Any]:
        return {
            "customer_id": session.customer_id,
            "customer_full_name": session.customer_full_name,
            "verified": session.verified,
            "verification_method": session.verification_method,
            "pending_intent": session.pending_intent,
            "order_id": session.order_id,
            "ticket_id": session.ticket_id,
            "callback_time": session.callback_time,
            "information_attempts": dict(session.information_attempts),
            "recognition_repairs": dict(session.recognition_repairs),
            "declined_information": list(session.declined_information),
        }

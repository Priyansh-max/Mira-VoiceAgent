from __future__ import annotations

import re
import time
from datetime import date, datetime, time as datetime_time, timedelta
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

_SPOKEN_DIGIT_KEYS = {"order_id", "case_id", "ticket_id"}


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
        if callback_time and tool_name != "support_callback":
            session.callback_time = self._resolve_callback_time(callback_time)

        if tool_name == "customer_identity":
            action, key, information = self._identity(
                session=session,
                purpose=purpose,
                caller_name=caller_name,
                caller_phone=caller_phone,
                attempt=attempt,
            )
        elif tool_name == "customer_lookup":
            action, key, information = self._lookup(
                session=session,
                purpose=purpose,
                order_id=order_id,
                ticket_id=ticket_id,
            )
        else:
            action, key, information = self._callback(
                session=session,
                purpose=purpose,
                callback_time=callback_time,
            )

        response_mode = get_response_mode()
        directive = None
        if key is not None:
            directive_text = DIRECTIVES[key].format(**self._speech_information(information))
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

        self._reset_identity_if_name_changed(session, caller_name)
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

    def _lookup(
        self,
        *,
        session: SessionState,
        purpose: str,
        order_id: str,
        ticket_id: str,
    ) -> tuple[str, str | None, Dict[str, Any]]:
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
            effective_order_id = order_id or session.order_id or ""
            if not effective_order_id:
                return self._request_or_repair(
                    session=session,
                    field_name="order_id",
                    action="request_order_id",
                    initial_key="customer_lookup.order_status.no_order_id",
                    repair_key="customer_lookup.order_status.repeat_order_id",
                    supplied_attempt=0,
                )
            session.order_id = effective_order_id
            result = get_order_status(
                effective_order_id,
                customer_id=session.customer_id,
                verified=session.verified,
            )
            if result.get("status") != "success":
                return (
                    "route_to_callback",
                    "customer_lookup.order_status.not_found",
                    {"order_id": effective_order_id},
                )
            record = result["record"]
            return (
                "return_order_status",
                "customer_lookup.order_status.success",
                {"order_id": record["order_id"], "status": record["status"]},
            )

        effective_ticket_id = ticket_id or session.ticket_id or ""
        if not effective_ticket_id:
            return self._request_or_repair(
                session=session,
                field_name="ticket_id",
                action="request_ticket_id",
                initial_key="customer_lookup.ticket_status.no_ticket_id",
                repair_key="customer_lookup.ticket_status.repeat_ticket_id",
                supplied_attempt=0,
            )
        session.ticket_id = effective_ticket_id
        result = lookup_ticket(
            effective_ticket_id,
            customer_id=session.customer_id,
            verified=session.verified,
        )
        if result.get("status") != "success":
            return (
                "route_to_callback",
                "customer_lookup.ticket_status.not_found",
                {"case_id": effective_ticket_id},
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
        requested_time = self._resolve_callback_time(callback_time or session.callback_time or "")
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
    def _resolve_callback_time(value: str, *, today: date | None = None) -> str:
        text = value.strip()
        if not text:
            return ""

        base_date = today or date.today()
        relative_days = (
            (r"\bday\s+after\s+tomorrow\b", 2),
            (r"\btomorrow\b", 1),
            (r"\btoday\b", 0),
        )
        for pattern, days in relative_days:
            if not re.search(pattern, text, flags=re.IGNORECASE):
                continue
            target = base_date + timedelta(days=days)
            remainder = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)
            remainder = remainder.strip(" ,.-")
            resolved = f"{target.strftime('%B')} {target.day}, {target.year}"
            if not remainder:
                return resolved
            lowered = remainder.lower()
            after_time = RealtimeToolRouter._resolve_after_time(remainder)
            if after_time:
                suffix = f"at {after_time}"
            elif lowered.startswith(("at ", "in ", "on ")):
                suffix = remainder
            elif re.search(r"\d|a\.?m\.?|p\.?m\.?|noon|midnight", lowered):
                suffix = f"at {remainder}"
            elif lowered in {"morning", "afternoon", "evening", "night"}:
                suffix = f"in the {lowered}"
            else:
                suffix = remainder
            return f"{resolved} {suffix}"
        return text

    @staticmethod
    def _resolve_after_time(value: str) -> str:
        match = re.fullmatch(
            r"after\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)",
            value.strip(),
            flags=re.IGNORECASE,
        )
        if not match:
            return ""

        hour = int(match.group(1))
        minute = int(match.group(2) or "0")
        if hour < 1 or hour > 12 or minute > 59:
            return ""

        meridiem = match.group(3).lower().replace(".", "")
        if meridiem == "pm" and hour != 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0

        slot = datetime.combine(date(2000, 1, 1), datetime_time(hour, minute)) + timedelta(minutes=30)
        display_hour = slot.hour % 12 or 12
        display_meridiem = "AM" if slot.hour < 12 else "PM"
        return f"{display_hour}:{slot.minute:02d} {display_meridiem}"

    @classmethod
    def _speech_information(cls, information: Dict[str, Any]) -> Dict[str, Any]:
        speech_ready = dict(information)
        for key in _SPOKEN_DIGIT_KEYS:
            value = speech_ready.get(key)
            if isinstance(value, str) and re.fullmatch(r"\d{2,}", value):
                speech_ready[key] = ", ".join(value)
        return speech_ready

    @staticmethod
    def _phone_last4(value: str) -> str:
        digits = re.sub(r"\D", "", value)
        return digits[-4:] if len(digits) >= 4 else ""

    @staticmethod
    def _reset_identity_if_name_changed(session: SessionState, caller_name: str) -> None:
        previous = (session.claimed_name or "").strip().casefold()
        current = caller_name.strip().casefold()
        if not previous or previous == current:
            return
        session.customer_id = None
        session.customer_full_name = None
        session.verified = False
        session.verification_method = None
        session.candidate_customer_ids = []
        session.phone_last4 = None
        session.last_verification_outcome = None

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

from __future__ import annotations

from datetime import date as real_date
import unittest
from unittest.mock import patch

from backend.conversation import SessionState
from backend.realtime_tools import RealtimeToolRouter


class RealtimeToolRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = RealtimeToolRouter()
        self.session = SessionState(session_id="test-session")

    def call(
        self,
        tool: str,
        *,
        purpose: str = "order_status",
        name: str | None = None,
        phone: str | None = None,
        order_id: str | None = None,
        ticket_id: str | None = None,
        callback_time: str | None = None,
        attempt: int = 0,
    ) -> dict:
        return self.router.execute(
            session=self.session,
            tool_name=tool,
            tool_args={
                "purpose": purpose,
                "caller_name": name,
                "caller_phone": phone,
                "order_id": order_id,
                "ticket_id": ticket_id,
                "callback_time": callback_time,
                "attempt": attempt,
            },
        )

    def test_missing_name_is_requested_once(self) -> None:
        first = self.call("customer_identity")
        self.assertEqual(first["action"], "identify_caller")
        self.assertIsNone(first["caller_name"])
        self.assertEqual(first["directive"]["key"], "customer_identity.order_status.no_caller_name")

        repair = self.call("customer_identity", attempt=0)
        self.assertEqual(repair["action"], "identify_caller")
        self.assertTrue(repair["information"]["recognition_repair"])
        self.assertIn("once more", repair["directive"]["response_text"])

        exhausted = self.call("customer_identity", attempt=1)
        self.assertEqual(exhausted["action"], "route_to_callback")
        self.assertIsNone(exhausted["directive"])
        self.assertEqual(self.session.declined_information, [])

    def test_unique_name_does_not_request_phone(self) -> None:
        result = self.call("customer_identity", name="John Carper")
        self.assertEqual(result["action"], "ready_for_lookup")
        self.assertIsNone(result["directive"])
        self.assertEqual(self.session.customer_id, "cust_1002")
        self.assertEqual(self.session.verification_method, "unique_name")
        self.assertNotIn("caller_phone", self.session.information_attempts)

    def test_multiple_name_matches_request_phone_once_then_escalate(self) -> None:
        first = self.call("customer_identity", name="John", attempt=1)
        self.assertEqual(first["action"], "verify_caller")
        self.assertEqual(first["directive"]["key"], "customer_identity.order_status.verify_name")

        repair = self.call("customer_identity", name="John", attempt=1)
        self.assertEqual(repair["action"], "verify_caller")
        self.assertTrue(repair["information"]["recognition_repair"])

        exhausted = self.call("customer_identity", name="John", attempt=1)
        self.assertEqual(exhausted["action"], "route_to_callback")
        self.assertIsNone(exhausted["directive"])

    def test_multiple_name_matches_can_be_verified_by_phone(self) -> None:
        self.call("customer_identity", name="John")
        result = self.call("customer_identity", name="John", phone="1198", attempt=1)
        self.assertEqual(result["action"], "ready_for_lookup")
        self.assertIsNone(result["directive"])
        self.assertEqual(self.session.customer_id, "cust_1002")
        self.assertEqual(self.session.verification_method, "name_and_phone")

    def test_complete_phone_number_is_accepted_for_verification(self) -> None:
        self.call("customer_identity", name="John")
        result = self.call(
            "customer_identity",
            name="John",
            phone="+1 212 555 1198",
            attempt=1,
        )

        self.assertEqual(result["action"], "ready_for_lookup")
        self.assertEqual(self.session.customer_id, "cust_1002")

    def test_fuzzy_name_match_requires_phone_before_lookup(self) -> None:
        candidate = self.call("customer_identity", name="Jon Carper")
        self.assertEqual(candidate["action"], "verify_caller")
        self.assertFalse(self.session.verified)

        verified = self.call(
            "customer_identity",
            name="Jon Carper",
            phone="1198",
            attempt=1,
        )
        self.assertEqual(verified["action"], "ready_for_lookup")
        self.assertEqual(self.session.customer_id, "cust_1002")
        self.assertEqual(self.session.verification_method, "name_and_phone")

    def test_lookup_uses_identified_customer_and_current_purpose(self) -> None:
        self.call("customer_identity", name="John Carper")
        missing = self.call("customer_lookup", name="John Carper")
        self.assertEqual(missing["action"], "request_order_id")

        result = self.call("customer_lookup", name="John Carper", order_id="1234", attempt=1)
        self.assertEqual(result["purpose"], "order_status")
        self.assertEqual(result["action"], "return_order_status")
        self.assertEqual(result["information"], {"order_id": "1234", "status": "Shipped today"})
        self.assertIn("1, 2, 3, 4", result["directive"]["response_text"])
        self.assertNotIn("one thousand", result["directive"]["response_text"].lower())

    def test_missing_order_id_gets_one_recognition_repair_then_escalates(self) -> None:
        self.call("customer_identity", name="John Carper")
        first = self.call("customer_lookup")
        self.assertEqual(first["action"], "request_order_id")
        self.assertEqual(first["directive"]["key"], "customer_lookup.order_status.no_order_id")

        repair = self.call("customer_lookup", attempt=0)
        self.assertEqual(repair["action"], "request_order_id")
        self.assertTrue(repair["information"]["recognition_repair"])

        exhausted = self.call("customer_lookup", attempt=1)
        self.assertEqual(exhausted["action"], "route_to_callback")
        self.assertIsNone(exhausted["directive"])

    def test_missing_ticket_id_gets_one_recognition_repair_then_escalates(self) -> None:
        self.call("customer_identity", purpose="ticket_status", name="John Carper")
        first = self.call("customer_lookup", purpose="ticket_status")
        self.assertEqual(first["action"], "request_ticket_id")
        self.assertEqual(first["directive"]["key"], "customer_lookup.ticket_status.no_ticket_id")

        repair = self.call("customer_lookup", purpose="ticket_status", attempt=0)
        self.assertEqual(repair["action"], "request_ticket_id")
        self.assertTrue(repair["information"]["recognition_repair"])

        exhausted = self.call("customer_lookup", purpose="ticket_status", attempt=1)
        self.assertEqual(exhausted["action"], "route_to_callback")
        self.assertIsNone(exhausted["directive"])

    def test_record_id_supplied_during_identity_is_carried_to_lookup(self) -> None:
        identity = self.call("customer_identity", name="John Carper", order_id="1234")
        self.assertEqual(identity["order_id"], "1234")

        result = self.call("customer_lookup")
        self.assertEqual(result["action"], "return_order_status")

    def test_unknown_order_routes_without_directive_format_error(self) -> None:
        self.call("customer_identity", name="John Carper")

        result = self.call("customer_lookup", order_id="999999", attempt=1)

        self.assertEqual(result["action"], "route_to_callback")
        self.assertEqual(result["information"]["order_id"], "999999")
        self.assertIn("9, 9, 9, 9, 9, 9", result["directive"]["response_text"])

    def test_callback_asks_for_time_before_scheduling(self) -> None:
        first = self.call("support_callback", purpose="ticket_status", attempt=1)
        self.assertEqual(first["purpose"], "ticket_status")
        self.assertEqual(first["action"], "request_callback_time")
        self.assertIsNone(first["callback_time"])
        self.assertEqual(
            first["directive"]["key"],
            "support_callback.ticket_status.no_callback_time",
        )

        with patch("backend.realtime_tools.date") as mocked_date:
            mocked_date.today.return_value = real_date(2026, 9, 30)
            scheduled = self.call(
                "support_callback",
                purpose="ticket_status",
                callback_time="tomorrow at 3 PM",
                attempt=1,
            )
        self.assertEqual(scheduled["action"], "schedule_callback")
        self.assertEqual(scheduled["callback_time"], "October 1, 2026 at 3 PM")
        self.assertEqual(scheduled["information"]["time"], "October 1, 2026 at 3 PM")
        self.assertIn("October 1, 2026 at 3 PM", scheduled["directive"]["response_text"])

    def test_relative_callback_time_resolves_today_and_day_after_tomorrow(self) -> None:
        today = real_date(2026, 9, 30)

        self.assertEqual(
            self.router._resolve_callback_time("today at 5 PM", today=today),
            "September 30, 2026 at 5 PM",
        )
        self.assertEqual(
            self.router._resolve_callback_time("day after tomorrow morning", today=today),
            "October 2, 2026 in the morning",
        )
        self.assertEqual(
            self.router._resolve_callback_time("tomorrow after 6 pm", today=today),
            "October 1, 2026 at 6:30 PM",
        )

    def test_callback_time_is_only_requested_once(self) -> None:
        self.call("support_callback", purpose="customer_support")
        repair = self.call("support_callback", purpose="customer_support", attempt=1)
        scheduled = self.call("support_callback", purpose="customer_support", attempt=1)

        self.assertEqual(repair["action"], "request_callback_time")
        self.assertTrue(repair["information"]["recognition_repair"])
        self.assertEqual(scheduled["action"], "schedule_callback")
        self.assertEqual(scheduled["callback_time"], "the next available time")
        self.assertEqual(self.session.declined_information, [])

    def test_purpose_can_change_during_the_same_call(self) -> None:
        self.call("customer_identity", purpose="order_status", name="John Carper")
        order = self.call(
            "customer_lookup",
            purpose="order_status",
            name="John Carper",
            order_id="1234",
        )
        ticket = self.call(
            "customer_lookup",
            purpose="ticket_status",
            name="John Carper",
            ticket_id="4821",
        )

        self.assertEqual(order["action"], "return_order_status")
        self.assertEqual(ticket["purpose"], "ticket_status")
        self.assertEqual(ticket["action"], "return_ticket_status")
        self.assertIn("4, 8, 2, 1", ticket["directive"]["response_text"])
        self.assertEqual(self.session.pending_intent, "ticket_status")

    def test_changed_name_resets_identity_before_new_match(self) -> None:
        self.call("customer_identity", name="John Carper")
        self.assertEqual(self.session.customer_id, "cust_1002")

        result = self.call("customer_identity", name="Arjun Mehta")

        self.assertEqual(result["action"], "ready_for_lookup")
        self.assertEqual(self.session.customer_id, "cust_1004")
        self.assertEqual(self.session.customer_full_name, "Arjun Mehta")


if __name__ == "__main__":
    unittest.main()

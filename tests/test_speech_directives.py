from __future__ import annotations

import unittest

from backend.speech_directives import DIRECTIVES


class SpeechDirectiveTest(unittest.TestCase):
    def test_directives_are_a_flat_string_registry(self) -> None:
        self.assertGreaterEqual(len(DIRECTIVES), 20)
        self.assertTrue(all(isinstance(key, str) for key in DIRECTIVES))
        self.assertTrue(all(isinstance(value, str) for value in DIRECTIVES.values()))

    def test_required_identity_routes_are_explicit(self) -> None:
        self.assertIn(
            "full name",
            DIRECTIVES["customer_identity.order_status.no_caller_name"],
        )
        self.assertIn(
            "last four digits",
            DIRECTIVES["customer_identity.order_status.verify_name"],
        )
        self.assertNotIn("customer_identity.order_status.identity_ready", DIRECTIVES)
        self.assertIn("order ID", DIRECTIVES["customer_lookup.order_status.no_order_id"])
        self.assertIn("ticket ID", DIRECTIVES["customer_lookup.ticket_status.no_ticket_id"])
        self.assertIn(
            "day and time",
            DIRECTIVES["support_callback.customer_support.no_callback_time"],
        )
        self.assertIn(
            "digits one at a time",
            DIRECTIVES["customer_lookup.order_status.repeat_order_id"],
        )

    def test_result_directive_uses_plain_format_placeholders(self) -> None:
        text = DIRECTIVES["customer_lookup.ticket_status.success"].format(
            case_id="4821",
            status="Refund pending",
            priority="High",
        )
        self.assertIn("ticket 4821 is Refund pending", text)


if __name__ == "__main__":
    unittest.main()

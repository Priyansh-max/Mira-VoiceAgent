from __future__ import annotations

import unittest

from backend.conversation import ConversationStore, SessionState, expected_information_field


class ExpectedInformationFieldTest(unittest.TestCase):
    def test_uses_current_order_capture_state(self) -> None:
        session = SessionState(
            session_id="test-session",
            pending_intent="order_status",
            information_attempts={"order_id": 1},
        )

        self.assertEqual(expected_information_field(session), "order_id")

    def test_callback_time_takes_priority_after_escalation(self) -> None:
        session = SessionState(
            session_id="test-session",
            pending_intent="order_status",
            information_attempts={"order_id": 1, "callback_time": 1},
        )

        self.assertEqual(expected_information_field(session), "callback_time")

    def test_returns_none_after_value_is_captured(self) -> None:
        session = SessionState(
            session_id="test-session",
            pending_intent="order_status",
            order_id="1234",
            information_attempts={"order_id": 1},
        )

        self.assertIsNone(expected_information_field(session))


class ConversationStoreTest(unittest.TestCase):
    def test_delete_session_removes_all_call_state_and_is_idempotent(self) -> None:
        store = ConversationStore()
        session = store.create_session()
        session.claimed_name = "Arjun Mehta"
        session.order_id = "12345"
        session.conversation_history.append({"role": "user", "content": "hello"})

        self.assertTrue(store.delete_session(session.session_id))
        self.assertFalse(store.delete_session(session.session_id))
        with self.assertRaises(KeyError):
            store.get_session(session.session_id)


if __name__ == "__main__":
    unittest.main()

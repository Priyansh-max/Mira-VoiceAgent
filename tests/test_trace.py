from __future__ import annotations

import unittest

from backend.trace import TraceEvent, TraceStore


class TraceStoreTest(unittest.TestCase):
    def test_clear_session_removes_history_and_subscribers(self) -> None:
        store = TraceStore()
        session_id = "completed-call"
        store.activate_session(session_id)
        queue = store.subscribe(session_id)
        store.emit(
            TraceEvent(
                session_id=session_id,
                type="tool",
                message="lookup complete",
            )
        )

        self.assertEqual(len(store.history(session_id)), 1)
        self.assertFalse(queue.empty())
        self.assertTrue(store.clear_session(session_id))
        self.assertEqual(store.history(session_id), [])
        self.assertFalse(store.clear_session(session_id))

        store.emit(
            TraceEvent(
                session_id=session_id,
                type="late_event",
                message="an in-flight request completed after disconnect",
            )
        )
        self.assertEqual(store.history(session_id), [])


if __name__ == "__main__":
    unittest.main()

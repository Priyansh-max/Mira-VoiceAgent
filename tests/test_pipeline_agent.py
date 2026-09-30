from __future__ import annotations

import json
import unittest

from backend.conversation import SessionState
from backend.pipeline_agent import ChainedPipelineAgent, _forced_tool_for_turn


class PipelineToolTraceTest(unittest.TestCase):
    def test_records_tool_name_input_and_response(self) -> None:
        agent = ChainedPipelineAgent()
        session = SessionState(session_id="test-session")
        tool_input = {
            "purpose": "order_status",
            "caller_name": None,
            "caller_phone": None,
            "order_id": None,
            "ticket_id": None,
            "callback_time": None,
            "attempt": 0,
        }

        result, events = agent._run_tool_chain(
            session=session,
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "customer_identity",
                        "arguments": json.dumps(tool_input),
                    },
                }
            ],
        )

        self.assertEqual(result["action"], "identify_caller")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["tool_name"], "customer_identity")
        self.assertEqual(events[0]["input"], tool_input)
        self.assertEqual(events[0]["response"]["action"], "identify_caller")
        self.assertNotIn("session_state", events[0]["response"])

    def test_order_status_intent_forces_identity_tool(self) -> None:
        session = SessionState(session_id="test-session")

        self.assertEqual(
            _forced_tool_for_turn(session, "I want the status of my order"),
            "customer_identity",
        )

    def test_expected_order_id_forces_lookup_tool(self) -> None:
        session = SessionState(
            session_id="test-session",
            verified=True,
            customer_id="cust_1004",
            pending_intent="order_status",
            information_attempts={"order_id": 1},
        )

        self.assertEqual(_forced_tool_for_turn(session, "one two three four"), "customer_lookup")

    def test_explicit_refusal_forces_support_callback(self) -> None:
        session = SessionState(
            session_id="test-session",
            pending_intent="order_status",
            information_attempts={"order_id": 1},
        )

        self.assertEqual(
            _forced_tool_for_turn(session, "I don't want to provide that"),
            "support_callback",
        )

    def test_empty_identifier_transcript_returns_deterministic_repair(self) -> None:
        agent = ChainedPipelineAgent()
        session = SessionState(session_id="test-session")
        agent.router.execute(
            session=session,
            tool_name="customer_identity",
            tool_args={
                "purpose": "order_status",
                "caller_name": "John Carper",
                "caller_phone": None,
                "order_id": None,
                "ticket_id": None,
                "callback_time": None,
                "attempt": 0,
            },
        )
        agent.router.execute(
            session=session,
            tool_name="customer_lookup",
            tool_args={
                "purpose": "order_status",
                "caller_name": "John Carper",
                "caller_phone": None,
                "order_id": None,
                "ticket_id": None,
                "callback_time": None,
                "attempt": 0,
            },
        )

        result = agent.handle_unrecognized_capture(
            session=session,
            expected_field="order_id",
        )

        self.assertEqual(result["tool_result"]["action"], "request_order_id")
        self.assertIn("digits one at a time", result["response_text"])
        self.assertIsNone(result["llm_ttft_ms"])

    def test_directive_route_to_callback_is_spoken_before_callback_tool(self) -> None:
        agent = ChainedPipelineAgent()
        session = SessionState(session_id="test-session")
        tool_input = {
            "purpose": "order_status",
            "caller_name": "Unknown Person",
            "caller_phone": None,
            "order_id": None,
            "ticket_id": None,
            "callback_time": None,
            "attempt": 0,
        }

        result, events = agent._run_tool_chain(
            session=session,
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "customer_identity",
                        "arguments": json.dumps(tool_input),
                    },
                }
            ],
        )

        self.assertEqual(result["action"], "route_to_callback")
        self.assertEqual(
            result["directive"]["key"],
            "customer_identity.order_status.no_name_match",
        )
        self.assertIn("Would you like me to set up a callback?", result["directive"]["response_text"])
        self.assertEqual([event["tool_name"] for event in events], ["customer_identity"])

    def test_llm_mode_tool_trace_omits_directive_text(self) -> None:
        agent = ChainedPipelineAgent()
        session = SessionState(session_id="test-session")
        tool_input = {
            "purpose": "order_status",
            "caller_name": "Unknown Person",
            "caller_phone": None,
            "order_id": None,
            "ticket_id": None,
            "callback_time": None,
            "attempt": 0,
        }

        result, events = agent._run_tool_chain(
            session=session,
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "customer_identity",
                        "arguments": json.dumps(tool_input),
                    },
                }
            ],
            include_directive=False,
        )

        self.assertEqual(result["action"], "route_to_callback")
        self.assertIsNotNone(result["directive"])
        self.assertNotIn("directive", events[0]["response"])


if __name__ == "__main__":
    unittest.main()

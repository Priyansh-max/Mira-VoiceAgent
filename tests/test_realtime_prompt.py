from __future__ import annotations

import unittest

from backend.prompts import REALTIME_AGENT_PROMPT
from backend.realtime import _session_config


class RealtimePromptTest(unittest.TestCase):
    def test_uses_the_five_prompt_sections_in_order(self) -> None:
        tags = ["identity", "behavior", "tools", "principles", "guardrails"]
        positions = [REALTIME_AGENT_PROMPT.index(f"<{tag}>") for tag in tags]

        self.assertEqual(positions, sorted(positions))
        for tag in tags:
            self.assertIn(f"</{tag}>", REALTIME_AGENT_PROMPT)

    def test_general_conversation_and_tool_output_rules_are_explicit(self) -> None:
        self.assertIn("reply directly without a tool", REALTIME_AGENT_PROMPT)
        self.assertIn("directive.require_repeat_verbatim", REALTIME_AGENT_PROMPT)
        self.assertIn("speak exactly `directive.response_text`", REALTIME_AGENT_PROMPT)
        self.assertIn("Never repeat a normal request for information", REALTIME_AGENT_PROMPT)
        self.assertIn("one digit at a time", REALTIME_AGENT_PROMPT)
        self.assertIn("Missing, truncated, empty, or unclear transcription is not a refusal", REALTIME_AGENT_PROMPT)
        self.assertIn("call `support_callback` immediately", REALTIME_AGENT_PROMPT)
        self.assertIn("`order_status`, `ticket_status`, or `customer_support`", REALTIME_AGENT_PROMPT)
        self.assertIn("Reuse the existing purpose by default", REALTIME_AGENT_PROMPT)
        self.assertIn("Do not infer a purpose change from vague wording", REALTIME_AGENT_PROMPT)
        self.assertIn("All seven keys are required", REALTIME_AGENT_PROMPT)
        self.assertIn("send JSON null", REALTIME_AGENT_PROMPT)
        self.assertIn("request_callback_time", REALTIME_AGENT_PROMPT)

    def test_realtime_session_keeps_context_without_summarization(self) -> None:
        config = _session_config()
        self.assertEqual(config["instructions"], REALTIME_AGENT_PROMPT)
        self.assertEqual(
            config["truncation"],
            {"type": "retention_ratio", "retention_ratio": 0.8},
        )

    def test_realtime_tools_omit_chat_completions_strict_field(self) -> None:
        for schema in _session_config()["tools"]:
            self.assertNotIn("strict", schema)
            self.assertFalse(schema["parameters"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()

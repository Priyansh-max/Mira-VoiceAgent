from __future__ import annotations

import unittest

from backend.tool_contract import PURPOSES, grouped_tool_schemas


class ToolContractTest(unittest.TestCase):
    def test_exposes_exactly_three_tools(self) -> None:
        schemas = grouped_tool_schemas()
        self.assertEqual(
            [schema["name"] for schema in schemas],
            ["customer_identity", "customer_lookup", "support_callback"],
        )

    def test_every_tool_uses_the_same_strict_input_contract(self) -> None:
        for schema in grouped_tool_schemas():
            parameters = schema["parameters"]
            self.assertTrue(schema["strict"])
            self.assertFalse(parameters["additionalProperties"])
            self.assertEqual(
                parameters["required"],
                [
                    "purpose",
                    "caller_name",
                    "caller_phone",
                    "order_id",
                    "ticket_id",
                    "callback_time",
                    "attempt",
                ],
            )
            self.assertEqual(parameters["properties"]["purpose"]["enum"], PURPOSES)
            self.assertEqual(parameters["properties"]["caller_name"]["type"], ["string", "null"])
            self.assertEqual(parameters["properties"]["caller_phone"]["type"], ["string", "null"])
            self.assertEqual(parameters["properties"]["order_id"]["type"], ["string", "null"])
            self.assertEqual(parameters["properties"]["ticket_id"]["type"], ["string", "null"])
            self.assertEqual(parameters["properties"]["callback_time"]["type"], ["string", "null"])
            self.assertEqual(parameters["properties"]["attempt"]["minimum"], 0)


if __name__ == "__main__":
    unittest.main()

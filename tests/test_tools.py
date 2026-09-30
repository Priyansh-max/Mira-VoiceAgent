from __future__ import annotations

import unittest

from backend.tools import identify_customer, resolve_customer_identity


class CustomerNameMatchingTest(unittest.TestCase):
    def test_unique_normalized_exact_match_does_not_require_phone(self) -> None:
        result = identify_customer("JOHN-CARPER")

        self.assertEqual(result["status"], "single_match")
        self.assertFalse(result["requires_phone_verification"])
        self.assertEqual(result["matches"][0]["name_match_type"], "exact")

    def test_typo_tolerant_match_requires_phone(self) -> None:
        result = identify_customer("Jon Carper")

        self.assertEqual(result["status"], "single_match")
        self.assertTrue(result["requires_phone_verification"])
        self.assertEqual(result["matches"][0]["customer_id"], "cust_1002")
        self.assertGreaterEqual(result["matches"][0]["name_score"], 92)

    def test_partial_name_keeps_multiple_candidates(self) -> None:
        result = identify_customer("John")

        self.assertEqual(result["status"], "multiple_matches")
        self.assertTrue(result["requires_phone_verification"])
        self.assertEqual(
            {match["customer_id"] for match in result["matches"]},
            {"cust_1001", "cust_1002"},
        )

    def test_weak_name_has_no_match(self) -> None:
        self.assertEqual(identify_customer("Completely Different")["status"], "no_match")

    def test_phone_resolves_a_fuzzy_candidate(self) -> None:
        result = resolve_customer_identity(
            name_query="Jon Carper",
            phone_last4="1198",
            candidate_customer_ids=["cust_1002"],
        )

        self.assertEqual(result["status"], "single_match")
        self.assertEqual(result["matches"][0]["customer_id"], "cust_1002")


if __name__ == "__main__":
    unittest.main()

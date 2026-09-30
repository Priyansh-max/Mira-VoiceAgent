from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict

from rapidfuzz import fuzz

# CURRENT:
# These mock tools now behave more like real customer-support backend services.
# Sensitive records are linked to customers and require verification before
# details are returned.
#
# OLD:
# The earlier version only mapped ticket/order IDs directly to statuses with no
# ownership or identity checks.


CUSTOMERS: Dict[str, Dict[str, Any]] = {
    "cust_1001": {
        "customer_id": "cust_1001",
        "full_name": "John Doe",
        "phone": "+1 415 555 3321",
        "phone_last4": "3321",
        "email": "john.doe@example.com",
    },
    "cust_1002": {
        "customer_id": "cust_1002",
        "full_name": "John Carper",
        "phone": "+1 212 555 1198",
        "phone_last4": "1198",
        "email": "john.carper@example.com",
    },
    "cust_1003": {
        "customer_id": "cust_1003",
        "full_name": "Priya Sharma",
        "phone": "+91 98765 47784",
        "phone_last4": "7784",
        "email": "priya.sharma@example.com",
    },
    "cust_1004": {
        "customer_id": "cust_1004",
        "full_name": "Arjun Mehta",
        "phone": "+91 98100 24680",
        "phone_last4": "4680",
        "email": "arjun.mehta@example.com",
    },
    "cust_1005": {
        "customer_id": "cust_1005",
        "full_name": "Emily Carter",
        "phone": "+1 202 555 0147",
        "phone_last4": "0147",
        "email": "emily.carter@example.com",
    },
    "cust_1006": {
        "customer_id": "cust_1006",
        "full_name": "Miguel Santos",
        "phone": "+34 612 345 786",
        "phone_last4": "5786",
        "email": "miguel.santos@example.com",
    },
    "cust_1007": {
        "customer_id": "cust_1007",
        "full_name": "Aisha Khan",
        "phone": "+971 50 555 2714",
        "phone_last4": "2714",
        "email": "aisha.khan@example.com",
    },
    "cust_1008": {
        "customer_id": "cust_1008",
        "full_name": "Yuki Tanaka",
        "phone": "+81 90 1234 8462",
        "phone_last4": "8462",
        "email": "yuki.tanaka@example.com",
    },
    "cust_1009": {
        "customer_id": "cust_1009",
        "full_name": "Oliver Muller",
        "phone": "+49 151 2345 6039",
        "phone_last4": "6039",
        "email": "oliver.muller@example.com",
    },
    "cust_1010": {
        "customer_id": "cust_1010",
        "full_name": "Thandiwe Ndlovu",
        "phone": "+27 82 555 9146",
        "phone_last4": "9146",
        "email": "thandiwe.ndlovu@example.com",
    },
}

TICKETS: Dict[str, Dict[str, Any]] = {
    "4821": {
        "case_id": "4821",
        "customer_id": "cust_1002",
        "status": "Refund pending",
        "priority": "High",
    },
    "4822": {
        "case_id": "4822",
        "customer_id": "cust_1001",
        "status": "Under review",
        "priority": "Normal",
    },
    "5903": {"case_id": "5903", "customer_id": "cust_1003", "status": "Resolved", "priority": "Normal"},
    "5904": {"case_id": "5904", "customer_id": "cust_1004", "status": "Awaiting response", "priority": "High"},
    "5905": {"case_id": "5905", "customer_id": "cust_1005", "status": "Open", "priority": "Normal"},
    "5906": {"case_id": "5906", "customer_id": "cust_1006", "status": "Escalated", "priority": "High"},
    "5907": {"case_id": "5907", "customer_id": "cust_1007", "status": "In progress", "priority": "Normal"},
    "5908": {"case_id": "5908", "customer_id": "cust_1008", "status": "Waiting on carrier", "priority": "Normal"},
    "5909": {"case_id": "5909", "customer_id": "cust_1009", "status": "Refund approved", "priority": "Normal"},
    "5910": {"case_id": "5910", "customer_id": "cust_1010", "status": "Under review", "priority": "High"},
}

ORDERS: Dict[str, Dict[str, Any]] = {
    "1234": {
        "order_id": "1234",
        "customer_id": "cust_1002",
        "status": "Shipped today",
    },
    "1235": {
        "order_id": "1235",
        "customer_id": "cust_1001",
        "status": "Preparing for shipment",
    },
    "2203": {"order_id": "2203", "customer_id": "cust_1003", "status": "Delivered yesterday"},
    "2204": {"order_id": "2204", "customer_id": "cust_1004", "status": "Out for delivery"},
    "2205": {"order_id": "2205", "customer_id": "cust_1005", "status": "Processing"},
    "2206": {"order_id": "2206", "customer_id": "cust_1006", "status": "Held at customs"},
    "2207": {"order_id": "2207", "customer_id": "cust_1007", "status": "Shipped"},
    "2208": {"order_id": "2208", "customer_id": "cust_1008", "status": "Ready for pickup"},
    "2209": {"order_id": "2209", "customer_id": "cust_1009", "status": "Return received"},
    "2210": {"order_id": "2210", "customer_id": "cust_1010", "status": "Delayed in transit"},
}


def _mask_phone(last4: str) -> str:
    return f"***-***-{last4}"


NAME_FUZZY_CANDIDATE_THRESHOLD = 80.0
NAME_FUZZY_STRONG_THRESHOLD = 92.0


def _normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(character for character in decomposed if not unicodedata.combining(character))
    alphanumeric_words = re.sub(r"[^\w]+", " ", without_accents.casefold(), flags=re.UNICODE)
    return " ".join(alphanumeric_words.split())


def _customer_match(customer: Dict[str, Any], score: float, match_type: str) -> Dict[str, Any]:
    return {
        "customer_id": customer["customer_id"],
        "full_name": customer["full_name"],
        "masked_phone": _mask_phone(customer["phone_last4"]),
        "name_score": round(score, 1),
        "name_match_type": match_type,
    }


def identify_customer(name_query: str) -> Dict[str, Any]:
    normalized_query = _normalize_name(name_query)
    if not normalized_query:
        return {"status": "no_match", "matches": []}

    exact_matches = []
    fuzzy_matches = []
    for customer in CUSTOMERS.values():
        normalized_full_name = _normalize_name(customer["full_name"])
        if normalized_query == normalized_full_name:
            exact_matches.append(_customer_match(customer, 100.0, "exact"))
            continue

        score = float(fuzz.WRatio(normalized_query, normalized_full_name))
        if score >= NAME_FUZZY_CANDIDATE_THRESHOLD:
            match_type = "strong_fuzzy" if score >= NAME_FUZZY_STRONG_THRESHOLD else "possible_fuzzy"
            fuzzy_matches.append(_customer_match(customer, score, match_type))

    matches = exact_matches or sorted(
        fuzzy_matches,
        key=lambda match: (-float(match["name_score"]), str(match["full_name"])),
    )

    if not matches:
        return {"status": "no_match", "matches": []}
    requires_phone_verification = not exact_matches or len(matches) > 1
    if len(matches) == 1:
        return {
            "status": "single_match",
            "matches": matches,
            "requires_phone_verification": requires_phone_verification,
        }
    return {
        "status": "multiple_matches",
        "matches": matches,
        "requires_phone_verification": True,
    }


def verify_customer(customer_id: str, phone_last4: str) -> Dict[str, Any]:
    customer = CUSTOMERS.get(customer_id)
    if not customer:
        return {"status": "not_found"}
    if customer["phone_last4"] != phone_last4:
        return {"status": "failed", "customer_id": customer_id}
    return {
        "status": "verified",
        "customer_id": customer_id,
        "full_name": customer["full_name"],
    }


def resolve_customer_identity(
    *,
    name_query: str | None = None,
    phone_last4: str | None = None,
    candidate_customer_ids: list[str] | None = None,
) -> Dict[str, Any]:
    candidate_ids = candidate_customer_ids or list(CUSTOMERS.keys())

    matches = []
    normalized_query = _normalize_name(name_query or "")

    for customer_id in candidate_ids:
        customer = CUSTOMERS.get(customer_id)
        if not customer:
            continue

        normalized_full_name = _normalize_name(customer["full_name"])
        name_score = float(fuzz.WRatio(normalized_query, normalized_full_name)) if normalized_query else 100.0
        name_ok = not normalized_query or name_score >= NAME_FUZZY_CANDIDATE_THRESHOLD
        phone_ok = not phone_last4 or customer["phone_last4"] == phone_last4

        if name_ok and phone_ok:
            match_type = "exact" if normalized_query == normalized_full_name else "fuzzy"
            matches.append(_customer_match(customer, name_score, match_type))

    if not matches:
        return {"status": "no_match", "matches": []}
    if len(matches) == 1:
        return {"status": "single_match", "matches": matches}
    return {"status": "multiple_matches", "matches": matches}


def lookup_ticket(case_id: str, *, customer_id: str | None, verified: bool) -> Dict[str, Any]:
    ticket = TICKETS.get(case_id)
    if not ticket:
        return {"status": "not_found", "case_id": case_id}
    if not verified or not customer_id:
        return {"status": "verification_required", "case_id": case_id}
    if ticket["customer_id"] != customer_id:
        return {"status": "ownership_mismatch", "case_id": case_id}
    return {
        "status": "success",
        "case_id": case_id,
        "customer_id": customer_id,
        "record": {
            "case_id": case_id,
            "status": ticket["status"],
            "priority": ticket["priority"],
        },
    }


def get_order_status(order_id: str, *, customer_id: str | None, verified: bool) -> Dict[str, Any]:
    order = ORDERS.get(order_id)
    if not order:
        return {"status": "not_found", "order_id": order_id}
    if not verified or not customer_id:
        return {"status": "verification_required", "order_id": order_id}
    if order["customer_id"] != customer_id:
        return {"status": "ownership_mismatch", "order_id": order_id}
    return {
        "status": "success",
        "order_id": order_id,
        "customer_id": customer_id,
        "record": {
            "order_id": order_id,
            "status": order["status"],
        },
    }


def schedule_callback(
    when: str,
    *,
    customer_id: str | None = None,
    customer_name: str | None = None,
    reason: str = "general support",
) -> Dict[str, Any]:
    # Simulated scheduling with a human support queue
    return {
        "status": "success",
        "queue": "human_support",
        "time": when,
        "reason": reason,
        "customer_id": customer_id,
        "customer_name": customer_name,
    }

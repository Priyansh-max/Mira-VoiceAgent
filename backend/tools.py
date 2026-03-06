from __future__ import annotations

from typing import Any, Dict

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
        "phone_last4": "3321",
        "email": "john.doe@example.com",
    },
    "cust_1002": {
        "customer_id": "cust_1002",
        "full_name": "John Carper",
        "phone_last4": "1198",
        "email": "john.carper@example.com",
    },
    "cust_1003": {
        "customer_id": "cust_1003",
        "full_name": "Priya Sharma",
        "phone_last4": "7784",
        "email": "priya.sharma@example.com",
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
}


def _mask_phone(last4: str) -> str:
    return f"***-***-{last4}"


def identify_customer(name_query: str) -> Dict[str, Any]:
    parts = [part.strip().lower() for part in name_query.split() if part.strip()]
    if not parts:
        return {"status": "no_match", "matches": []}

    matches = []
    for customer in CUSTOMERS.values():
        full_name = customer["full_name"].lower()
        if all(part in full_name for part in parts):
            matches.append(
                {
                    "customer_id": customer["customer_id"],
                    "full_name": customer["full_name"],
                    "masked_phone": _mask_phone(customer["phone_last4"]),
                }
            )

    if not matches:
        return {"status": "no_match", "matches": []}
    if len(matches) == 1:
        return {"status": "single_match", "matches": matches}
    return {"status": "multiple_matches", "matches": matches}


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
    name_parts = [part.strip().lower() for part in (name_query or "").split() if part.strip()]

    for customer_id in candidate_ids:
        customer = CUSTOMERS.get(customer_id)
        if not customer:
            continue

        full_name = customer["full_name"].lower()
        name_ok = not name_parts or all(part in full_name for part in name_parts)
        phone_ok = not phone_last4 or customer["phone_last4"] == phone_last4

        if name_ok and phone_ok:
            matches.append(
                {
                    "customer_id": customer["customer_id"],
                    "full_name": customer["full_name"],
                    "masked_phone": _mask_phone(customer["phone_last4"]),
                }
            )

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
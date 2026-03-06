from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time
import uuid


@dataclass
class SessionState:
    session_id: str
    user_name: Optional[str] = None
    claimed_name: Optional[str] = None
    customer_id: Optional[str] = None
    customer_full_name: Optional[str] = None
    verified: bool = False
    verification_method: Optional[str] = None
    candidate_customer_ids: List[str] = field(default_factory=list)
    phone_last4: Optional[str] = None
    pending_intent: Optional[str] = None
    last_verification_outcome: Optional[str] = None
    last_failed_name: Optional[str] = None
    last_failed_phone_last4: Optional[str] = None
    last_policy_code: Optional[str] = None
    ticket_id: Optional[str] = None
    order_id: Optional[str] = None
    sentiment: Optional[str] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: time.time())


class ConversationStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, SessionState] = {}

    def create_session(self) -> SessionState:
        session_id = str(uuid.uuid4())
        session = SessionState(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> SessionState:
        try:
            return self._sessions[session_id]
        except KeyError as e:
            raise KeyError(f"Unknown session_id: {session_id}") from e
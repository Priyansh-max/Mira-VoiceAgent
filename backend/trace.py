from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Deque, Dict, List, Set

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    ts: float = Field(default_factory=lambda: time.time())
    session_id: str
    type: str
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)


class TraceStore:
    def __init__(self, *, history_limit: int = 200) -> None:
        self._history_limit = history_limit
        self._active_sessions: Set[str] = set()
        self._history: Dict[str, Deque[TraceEvent]] = {}
        self._subscribers: Dict[str, Set[asyncio.Queue[TraceEvent]]] = {}

    def activate_session(self, session_id: str) -> None:
        self._active_sessions.add(session_id)

    def history(self, session_id: str) -> List[TraceEvent]:
        return list(self._history.get(session_id, deque()))

    def emit(self, event: TraceEvent) -> None:
        if event.session_id not in self._active_sessions:
            return
        hist = self._history.setdefault(session_id := event.session_id, deque(maxlen=self._history_limit))
        hist.append(event)

        queues = self._subscribers.get(session_id)
        if not queues:
            return

        for q in list(queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # drop oldest then try once more
                try:
                    _ = q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def subscribe(self, session_id: str, *, max_queue: int = 100) -> asyncio.Queue[TraceEvent]:
        q: asyncio.Queue[TraceEvent] = asyncio.Queue(maxsize=max_queue)
        if session_id not in self._active_sessions:
            return q
        self._subscribers.setdefault(session_id, set()).add(q)
        return q

    def unsubscribe(self, session_id: str, q: asyncio.Queue[TraceEvent]) -> None:
        subs = self._subscribers.get(session_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subscribers.pop(session_id, None)

    def clear_session(self, session_id: str) -> bool:
        """Remove trace history and live subscribers belonging to one call."""
        was_active = session_id in self._active_sessions
        self._active_sessions.discard(session_id)
        history_removed = self._history.pop(session_id, None) is not None
        subscribers_removed = self._subscribers.pop(session_id, None) is not None
        return was_active or history_removed or subscribers_removed

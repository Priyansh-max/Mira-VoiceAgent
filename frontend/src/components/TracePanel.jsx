import { useEffect, useState } from 'react';
import { traceEventSource } from '../api';

const typeLabel = (type) => {
  const map = {
    session: 'Session',
    user_input: 'User input',
    user_identified: 'User identified',
    intent: 'Intent',
    sentiment: 'Sentiment',
    tool_call: 'Tool call',
    tool_result: 'Tool result',
    response: 'Response',
    realtime_session: 'Realtime session',
    realtime_event: 'Realtime event',
    user_transcript: 'User transcript',
    assistant_transcript: 'Assistant transcript',
    realtime_error: 'Realtime error',
  };
  return map[type] || type;
};

export default function TracePanel({ sessionId, externalEvents = [], onTraceError, compact = false }) {
  const [backendEvents, setBackendEvents] = useState([]);

  useEffect(() => {
    if (!sessionId) {
      setBackendEvents([]);
      return;
    }
    const unsub = traceEventSource(
      sessionId,
      (ev) => setBackendEvents((prev) => [...prev, ev]),
      onTraceError
    );
    return unsub;
  }, [sessionId, onTraceError]);

  const events = [...backendEvents, ...externalEvents].sort((a, b) => (a.ts || 0) - (b.ts || 0));

  return (
    <div className={`rounded-[22px] border border-white/65 bg-white/48 p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.68)] ${compact ? 'flex h-full min-h-0 flex-col' : 'min-h-[32rem]'}`}>
      <div className="mb-3 text-sm font-semibold text-slate-600">
        Agent trace {sessionId ? `(session ${sessionId.slice(0, 8)}…)` : ''}
      </div>
      <div className={`hide-scrollbar overflow-y-auto pr-1 ${compact ? 'min-h-0 flex-1' : 'h-[calc(100%-2rem)]'}`}>
        {events.length === 0 && sessionId && (
          <div className="text-sm text-slate-400">Listening for events… Send a message to see trace.</div>
        )}
        {events.length === 0 && !sessionId && (
          <div className="text-sm text-slate-400">Start a session to see the trace.</div>
        )}
        {events.map((ev, i) => (
          <div
            key={i}
            className="mb-3 rounded-2xl border-l-[3px] border-l-[#58a6b2] bg-[linear-gradient(180deg,rgba(247,251,251,0.94),rgba(241,247,247,0.88))] px-3 py-3 last:mb-0"
          >
            <div className="flex flex-wrap gap-2 text-sm">
              <span className="font-semibold text-[#2b7a78]">{typeLabel(ev.type)}</span>
              <span className="text-slate-500">{ev.message}</span>
            </div>
            {ev.data && Object.keys(ev.data).length > 0 && (
              <pre className="mt-2 whitespace-pre-wrap break-words text-[12px] text-slate-500">
                {JSON.stringify(ev.data)}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

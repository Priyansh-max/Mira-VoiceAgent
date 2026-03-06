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
  };
  return map[type] || type;
};

export default function TracePanel({ sessionId }) {
  const [events, setEvents] = useState([]);

  useEffect(() => {
    if (!sessionId) {
      setEvents([]);
      return;
    }
    const unsub = traceEventSource(
      sessionId,
      (ev) => setEvents((prev) => [...prev, ev]),
      () => {}
    );
    return unsub;
  }, [sessionId]);

  return (
    <div
      style={{
        flex: '1 1 280px',
        minHeight: 200,
        background: '#16213e',
        borderRadius: 8,
        padding: '0.75rem',
        overflow: 'auto',
        fontFamily: 'monospace',
        fontSize: '0.85rem',
      }}
    >
      <div style={{ marginBottom: '0.5rem', fontWeight: 600, color: '#a0a0a0' }}>
        Agent trace {sessionId ? `(session ${sessionId.slice(0, 8)}…)` : ''}
      </div>
      {events.length === 0 && sessionId && (
        <div style={{ color: '#666' }}>Listening for events… Send a message to see trace.</div>
      )}
      {events.length === 0 && !sessionId && (
        <div style={{ color: '#666' }}>Start a session to see the trace.</div>
      )}
      {events.map((ev, i) => (
        <div
          key={i}
          style={{
            marginBottom: '0.5rem',
            padding: '0.35rem 0.5rem',
            background: 'rgba(255,255,255,0.06)',
            borderRadius: 4,
            borderLeft: '3px solid #3498db',
          }}
        >
          <span style={{ color: '#3498db', fontWeight: 600 }}>{typeLabel(ev.type)}</span>
          <span style={{ color: '#bdc3c7', marginLeft: '0.5rem' }}>{ev.message}</span>
          {ev.data && Object.keys(ev.data).length > 0 && (
            <pre
              style={{
                margin: '0.25rem 0 0',
                fontSize: '0.75rem',
                color: '#95a5a6',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {JSON.stringify(ev.data)}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}

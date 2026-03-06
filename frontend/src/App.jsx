import { useState, useEffect, useCallback } from 'react';
import { createSession } from './api';
import ChatPanel from './components/ChatPanel';
import TracePanel from './components/TracePanel';

export default function App() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem('voice-agent-session') || '');
  const [error, setError] = useState(null);

  useEffect(() => {
    if (sessionId) localStorage.setItem('voice-agent-session', sessionId);
  }, [sessionId]);

  const ensureSession = useCallback(async () => {
    if (sessionId) return sessionId;
    const { session_id } = await createSession();
    setSessionId(session_id);
    return session_id;
  }, [sessionId]);

  useEffect(() => {
    ensureSession().catch((e) => setError(e.message));
  }, []);

  return (
    <div style={{ padding: '1rem', maxWidth: 900, margin: '0 auto', minHeight: '100vh', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
        <h1 style={{ margin: 0, fontSize: '1.5rem' }}>Voice Agent</h1>
        <button
          type="button"
          onClick={async () => {
            setError(null);
            const { session_id } = await createSession();
            setSessionId(session_id);
          }}
          style={{
            padding: '0.4rem 0.8rem',
            borderRadius: 6,
            border: '1px solid #444',
            background: 'transparent',
            color: '#eee',
            cursor: 'pointer',
          }}
        >
          New session
        </button>
      </header>
      {error && (
        <div style={{ padding: '0.5rem 0.75rem', background: 'rgba(231,76,60,0.2)', borderRadius: 6, color: '#e74c3c' }}>
          {error}
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', flex: 1, minHeight: 0 }}>
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <h2 style={{ margin: '0 0 0.5rem', fontSize: '1rem', color: '#bdc3c7' }}>Chat</h2>
          <ChatPanel sessionId={sessionId} onError={setError} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <h2 style={{ margin: '0 0 0.5rem', fontSize: '1rem', color: '#bdc3c7' }}>Trace</h2>
          <TracePanel sessionId={sessionId} />
        </div>
      </div>
    </div>
  );
}

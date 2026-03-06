import { useState, useEffect, useCallback, useRef } from 'react';
import { checkHealth, createSession } from './api';
import ChatPanel from './components/ChatPanel';
import RealtimeVoicePanel from './components/RealtimeVoicePanel';
import TracePanel from './components/TracePanel';


function currentRoute() {
  return window.location.pathname === '/legacy' ? '/legacy' : '/';
}

export default function App() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem('voice-agent-session') || '');
  const [error, setError] = useState(null);
  const [clientTraceEvents, setClientTraceEvents] = useState([]);
  const [route, setRoute] = useState(currentRoute);
  const [voiceResetToken, setVoiceResetToken] = useState(0);
  const recoveringSessionRef = useRef(false);

  useEffect(() => {
    if (sessionId) {
      localStorage.setItem('voice-agent-session', sessionId);
    } else {
      localStorage.removeItem('voice-agent-session');
    }
  }, [sessionId]);

  const ensureSession = useCallback(async () => {
    await checkHealth();
    if (sessionId) return sessionId;
    const { session_id } = await createSession();
    setSessionId(session_id);
    return session_id;
  }, [sessionId]);

  useEffect(() => {
    ensureSession().catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    const onPopState = () => setRoute(currentRoute());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const handleSessionChange = useCallback((nextSessionId) => {
    setClientTraceEvents([]);
    setSessionId(nextSessionId);
  }, []);

  const navigateTo = useCallback((nextRoute) => {
    if (nextRoute === route) return;
    window.history.pushState({}, '', nextRoute);
    setRoute(nextRoute);
  }, [route]);

  const startNewSession = useCallback(async () => {
    setError(null);
    setClientTraceEvents([]);
    setVoiceResetToken((prev) => prev + 1);
    setSessionId('');
    localStorage.removeItem('voice-agent-session');
    const { session_id } = await createSession();
    setSessionId(session_id);
  }, []);

  const recoverSession = useCallback(async () => {
    if (recoveringSessionRef.current) return;
    recoveringSessionRef.current = true;
    try {
      const { session_id } = await createSession();
      setVoiceResetToken((prev) => prev + 1);
      handleSessionChange(session_id);
      setError('Recovered from an expired in-memory backend session. A new session has been created.');
    } catch (e) {
      setError(e.message);
    } finally {
      recoveringSessionRef.current = false;
    }
  }, [handleSessionChange]);

  const pushClientTraceEvent = useCallback((event) => {
    setClientTraceEvents((prev) => [...prev.slice(-39), event]);
  }, []);

  return (
    <div className="app-shell">
      <div className="app-backdrop app-backdrop-left" />
      <div className="app-backdrop app-backdrop-right" />
      <div className="mx-auto flex h-screen w-full max-w-7xl flex-col gap-5 px-6 pb-4 pt-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl">
            <h1 className="text-4xl ml-4 font-semibold leading-tight tracking-[-0.03em] text-slate-900 sm:text-5xl">
              {route === '/legacy' ? 'Legacy Implementation' : 'Mint Customer Agent'}
            </h1>
            <p className="mt-3 max-w-2xl text-sm text-slate-500 sm:text-base">
              {route === '/legacy'
                ? 'The original text-first debugging flow lives here as a separate route.'
                : ''}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="rounded-full border border-white/60 bg-white/50 px-4 py-2.5 text-sm font-medium text-slate-700 shadow-[0_10px_24px_rgba(27,46,51,0.05)] backdrop-blur-xl transition hover:bg-white/70"
              onClick={() => navigateTo(route === '/legacy' ? '/' : '/legacy')}
            >
              {route === '/legacy' ? 'Back to voice demo' : 'Open legacy implementation'}
            </button>
            <button
              type="button"
              className="rounded-full bg-[#2b7a78] px-4 py-2.5 text-sm font-medium text-white shadow-[0_14px_28px_rgba(43,122,120,0.22)] transition hover:bg-[#256a68]"
              onClick={() => startNewSession().catch((e) => setError(e.message))}
            >
              New session
            </button>
          </div>
        </header>
        {error && (
          <div className="rounded-2xl border border-[#c96a5b]/15 bg-[#c96a5b]/10 px-4 py-3 text-sm text-[#8a4033] backdrop-blur-md">
            {error}
          </div>
        )}

        {route === '/legacy' ? (
          <section className="legacy-layout min-h-0 flex-1 overflow-hidden">
            <div className="debug-column">
              <h2 className="debug-title">Legacy text chat</h2>
              <ChatPanel key={`legacy-chat-${sessionId}`} sessionId={sessionId} onError={setError} />
            </div>
            <div className="debug-column">
              <h2 className="debug-title">Legacy trace</h2>
              <TracePanel
                key={`legacy-trace-${sessionId}`}
                sessionId={sessionId}
                externalEvents={clientTraceEvents}
                onTraceError={recoverSession}
              />
            </div>
          </section>
        ) : (
          <main className="relative min-h-0 flex-1 overflow-hidden rounded-[32px] border border-white/60 bg-white/40 p-4 shadow-[0_30px_80px_rgba(39,60,67,0.10)] backdrop-blur-[28px] sm:p-6">
            <div className="pointer-events-none absolute inset-0 rounded-[32px] shadow-[inset_0_1px_0_rgba(255,255,255,0.72)]" />
            <RealtimeVoicePanel
              sessionId={sessionId}
              resetToken={voiceResetToken}

              tracePanel={(
                <TracePanel
                  key={`trace-${sessionId}`}
                  sessionId={sessionId}
                  externalEvents={clientTraceEvents}
                  onTraceError={recoverSession}
                  compact
                />
              )}
              onSessionChange={handleSessionChange}
              onTraceEvent={pushClientTraceEvent}
              onError={setError}
            />
          </main>
        )}
      </div>
    </div>
  );
}

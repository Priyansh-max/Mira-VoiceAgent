import { useCallback, useEffect, useState } from 'react';
import ChainedVoicePanel from './components/ChainedVoicePanel';
import RealtimeVoicePanel from './components/RealtimeVoicePanel';

function VoiceControls({ voiceMode, responseMode, onModeChange, onResponseModeChange, onNewSession }) {
  return (
    <div className="embedded-voice-controls" aria-label="Voice implementation">
      <div className="embedded-mode-switch" aria-label="Transport mode">
        <button
          type="button"
          className={voiceMode === 'pipeline' ? 'active' : ''}
          onClick={() => onModeChange('pipeline')}
        >
          Pipeline
        </button>
        <button
          type="button"
          className={voiceMode === 'streaming' ? 'active' : ''}
          onClick={() => onModeChange('streaming')}
        >
          Streaming
        </button>
        <button
          type="button"
          className={voiceMode === 'realtime' ? 'active' : ''}
          onClick={() => onModeChange('realtime')}
        >
          Realtime
        </button>
      </div>
      <div className="embedded-mode-switch embedded-response-switch" aria-label="Response mode">
        <button
          type="button"
          className={responseMode === 'llm' ? 'active' : ''}
          onClick={() => onResponseModeChange('llm')}
        >
          LLM
        </button>
        <button
          type="button"
          className={responseMode === 'speech_directive' ? 'active' : ''}
          onClick={() => onResponseModeChange('speech_directive')}
        >
          Directive
        </button>
      </div>
      <button
        type="button"
        className="embedded-reset-button"
        onClick={onNewSession}
        aria-label="Start a new session"
        title="New session"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 8a8 8 0 1 0 1 6h-2.1A6 6 0 1 1 16.6 8H13l4-4 4 4h-2Z" /></svg>
      </button>
    </div>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState('');
  const [error, setError] = useState(null);
  const [voiceResetToken, setVoiceResetToken] = useState(0);
  const [voiceMode, setVoiceMode] = useState('pipeline');
  const [responseMode, setResponseMode] = useState('speech_directive');

  useEffect(() => {
    if (sessionId) {
      localStorage.setItem('mira-voice-session', sessionId);
    } else {
      localStorage.removeItem('mira-voice-session');
    }
  }, [sessionId]);

  const handleSessionChange = useCallback((nextSessionId) => {
    setSessionId(nextSessionId);
    setError(null);
  }, []);

  const startNewSession = useCallback(() => {
    setError(null);
    setSessionId('');
    localStorage.removeItem('mira-voice-session');
    setVoiceResetToken((current) => current + 1);
  }, []);

  const switchVoiceMode = useCallback((nextMode) => {
    setVoiceMode(nextMode);
    setError(null);
    setSessionId('');
    localStorage.removeItem('mira-voice-session');
    setVoiceResetToken((current) => current + 1);
  }, []);

  const switchResponseMode = useCallback((nextMode) => {
    setResponseMode(nextMode);
    setError(null);
    setSessionId('');
    localStorage.removeItem('mira-voice-session');
    setVoiceResetToken((current) => current + 1);
  }, []);

  const voiceControls = (
    <VoiceControls
      voiceMode={voiceMode}
      responseMode={responseMode}
      onModeChange={switchVoiceMode}
      onResponseModeChange={switchResponseMode}
      onNewSession={startNewSession}
    />
  );

  return (
    <div className="mira-app">
      <div className="ambient-orb ambient-orb-one" />
      <div className="ambient-orb ambient-orb-two" />
      <div className="ambient-grid" />

      {error && (
        <div className="error-toast" role="alert">
          <span className="error-toast-icon">!</span>
          <span>{error}</span>
          <button type="button" onClick={() => setError(null)} aria-label="Dismiss error">×</button>
        </div>
      )}

      <main className="mira-main">
        {voiceMode === 'pipeline' || voiceMode === 'streaming' ? (
          <ChainedVoicePanel
            sessionId={sessionId}
            resetToken={voiceResetToken}
            onSessionChange={handleSessionChange}
            onError={setError}
            controls={voiceControls}
            transportMode={voiceMode}
            responseMode={responseMode}
          />
        ) : (
          <RealtimeVoicePanel
            sessionId={sessionId}
            resetToken={voiceResetToken}
            onSessionChange={handleSessionChange}
            onError={setError}
            controls={voiceControls}
            responseMode={responseMode}
          />
        )}
      </main>
    </div>
  );
}

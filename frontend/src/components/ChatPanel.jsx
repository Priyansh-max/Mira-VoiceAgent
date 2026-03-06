import { useState, useRef, useCallback, useEffect } from 'react';
import { chat, transcribeAudio, synthesizeSpeech, ttsAvailable } from '../api';
import VoiceRecorder from './VoiceRecorder';

export default function ChatPanel({ sessionId, onError }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [ttsOn, setTtsOn] = useState(true);
  const [ttsSupported, setTtsSupported] = useState(false);
  const audioRef = useRef(null);

  const sendText = useCallback(
    async (text) => {
      if (!sessionId || !text?.trim()) return;
      setLoading(true);
      onError?.(null);
      try {
        const { response_text } = await chat(sessionId, text.trim());
        setMessages((prev) => [
          ...prev,
          { role: 'user', text: text.trim() },
          { role: 'assistant', text: response_text },
        ]);
        if (ttsOn && ttsSupported) {
          try {
            const blob = await synthesizeSpeech(response_text);
            const url = URL.createObjectURL(blob);
            const a = new Audio(url);
            audioRef.current = a;
            a.onended = () => URL.revokeObjectURL(url);
            await a.play();
          } catch (_) {}
        } else if (ttsOn && !ttsSupported && window.speechSynthesis) {
          const u = new SpeechSynthesisUtterance(response_text);
          window.speechSynthesis.speak(u);
        }
      } catch (e) {
        onError?.(e.message);
      } finally {
        setLoading(false);
      }
    },
    [sessionId, ttsOn, ttsSupported, onError]
  );

  const handleTranscript = useCallback(
    async (blob) => {
      onError?.(null);
      try {
        const { text, error } = await transcribeAudio(blob);
        if (error || !text) {
          onError?.(error || 'No speech detected. Try again or type below.');
          return;
        }
        await sendText(text);
      } catch (e) {
        onError?.(e.message);
      }
    },
    [sendText, onError]
  );

  useEffect(() => {
    ttsAvailable().then((r) => setTtsSupported(r.available ?? false));
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', minHeight: 0 }}>
      <div
        style={{
          flex: 1,
          minHeight: 120,
          overflow: 'auto',
          background: '#16213e',
          borderRadius: 8,
          padding: '0.75rem',
        }}
      >
        {messages.length === 0 && (
          <div style={{ color: '#666' }}>Send a voice or text message to start.</div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            style={{
              textAlign: m.role === 'user' ? 'right' : 'left',
              marginBottom: '0.5rem',
            }}
          >
            <span
              style={{
                display: 'inline-block',
                padding: '0.4rem 0.75rem',
                borderRadius: 8,
                maxWidth: '85%',
                background: m.role === 'user' ? '#3498db' : 'rgba(255,255,255,0.1)',
              }}
            >
              {m.text}
            </span>
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={ttsOn}
            onChange={(e) => setTtsOn(e.target.checked)}
          />
          <span>Speak response</span>
        </label>
      </div>
      <VoiceRecorder onTranscript={handleTranscript} disabled={!sessionId || loading} />
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <input
          type="text"
          placeholder="Or type a message…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && sendText(input)}
          disabled={!sessionId || loading}
          style={{
            flex: 1,
            padding: '0.5rem 0.75rem',
            borderRadius: 8,
            border: '1px solid #333',
            background: '#0f0f1a',
            color: '#eee',
          }}
        />
        <button
          type="button"
          onClick={() => sendText(input).then(() => setInput(''))}
          disabled={!sessionId || loading || !input.trim()}
          style={{
            padding: '0.5rem 1rem',
            borderRadius: 8,
            border: 'none',
            background: '#2ecc71',
            color: '#fff',
            cursor: 'pointer',
            fontWeight: 600,
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
}

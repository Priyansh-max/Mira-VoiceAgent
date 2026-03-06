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
    <div className="debug-panel">
      <div className="debug-feed">
        {messages.length === 0 && (
          <div className="empty-copy">Send a voice or text message to start.</div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`message-row ${m.role === 'user' ? 'user' : 'assistant'}`}
          >
            <span className={`message-bubble ${m.role === 'user' ? 'user' : 'assistant'}`}>
              {m.text}
            </span>
          </div>
        ))}
      </div>
      <div className="chat-toolbar">
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={ttsOn}
            onChange={(e) => setTtsOn(e.target.checked)}
          />
          <span>Speak response</span>
        </label>
      </div>
      <VoiceRecorder onTranscript={handleTranscript} disabled={!sessionId || loading} />
      <div className="composer-row">
        <input
          type="text"
          placeholder="Or type a message…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && sendText(input)}
          disabled={!sessionId || loading}
          className="composer-input"
        />
        <button
          type="button"
          onClick={() => sendText(input).then(() => setInput(''))}
          disabled={!sessionId || loading || !input.trim()}
          className="composer-button"
        >
          Send
        </button>
      </div>
    </div>
  );
}

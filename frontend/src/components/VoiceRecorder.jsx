import { useState, useRef, useCallback } from 'react';

const MIME = 'audio/webm;codecs=opus';

export default function VoiceRecorder({ onTranscript, disabled }) {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState(null);
  const streamRef = useRef(null);
  const recorderRef = useRef(null);

  const start = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream, { mimeType: MIME });
      recorderRef.current = recorder;
      const chunks = [];
      recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        if (chunks.length) onTranscript(new Blob(chunks, { type: MIME }));
      };
      recorder.start(200);
      setRecording(true);
    } catch (e) {
      setError(e.message || 'Microphone access failed');
    }
  }, [onTranscript]);

  const stop = useCallback(() => {
    if (recorderRef.current?.state === 'recording') {
      recorderRef.current.stop();
      recorderRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setRecording(false);
  }, []);

  return (
    <div className="recorder-row">
      <button
        type="button"
        onClick={recording ? stop : start}
        disabled={disabled}
        className={`recorder-button ${recording ? 'recording' : ''}`}
      >
        {recording ? 'Stop' : 'Hold to talk'}
      </button>
      {error && <span className="recorder-error">{error}</span>}
    </div>
  );
}

const API = import.meta.env.VITE_BACKEND_URL

export async function createSession() {
  const res = await fetch(`${API}/sessions`, { method: 'POST' });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function checkHealth() {
  const res = await fetch(`${API}/health`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function chat(sessionId, text) {
  const res = await fetch(`${API}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createRealtimeSession() {
  const res = await fetch(`${API}/realtime/session`, { method: 'POST' });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function executeRealtimeTool(sessionId, toolName, toolArgs = {}) {
  const res = await fetch(`${API}/realtime/tool`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      tool_name: toolName,
      tool_args: toolArgs,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function sttAvailable() {
  const res = await fetch(`${API}/stt/available`);
  if (!res.ok) return { available: false };
  return res.json();
}

export async function transcribeAudio(blob) {
  const form = new FormData();
  form.append('audio', blob, 'audio.webm');
  const res = await fetch(`${API}/stt`, { method: 'POST', body: form });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function ttsAvailable() {
  const res = await fetch(`${API}/tts/available`);
  if (!res.ok) return { available: false };
  return res.json();
}

export async function synthesizeSpeech(text) {
  const res = await fetch(`${API}/tts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.blob();
}

export function traceEventSource(sessionId, onEvent, onError) {
  const es = new EventSource(`${API}/trace/${sessionId}`);
  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(data);
    } catch (_) {}
  };
  es.onerror = () => {
    onError?.(new Error(`Trace stream failed for session ${sessionId}`));
    es.close();
  };
  return () => es.close();
}

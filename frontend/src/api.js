const API = import.meta.env.VITE_BACKEND_URL

export async function createPipelineSession() {
  const res = await fetch(`${API}/pipeline/session`, { method: 'POST' });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteSession(sessionId) {
  if (!sessionId) return { session_id: '', deleted: false };
  const res = await fetch(`${API}/session/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
    keepalive: true,
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function runPipelineTurn(sessionId, turnId, audioBlob, audioDurationMs = null) {
  const form = new FormData();
  const mediaType = (audioBlob.type || 'audio/webm').split(';', 1)[0];
  const extension = {
    'audio/mp4': 'mp4',
    'audio/ogg': 'ogg',
    'audio/wav': 'wav',
    'audio/mpeg': 'mp3',
    'audio/webm': 'webm',
  }[mediaType] || 'webm';
  form.append('session_id', sessionId);
  form.append('turn_id', turnId);
  if (Number.isFinite(audioDurationMs) && audioDurationMs > 0) {
    form.append('audio_duration_ms', String(audioDurationMs));
  }
  form.append('audio', audioBlob, `turn.${extension}`);
  const res = await fetch(`${API}/pipeline/turn`, {
    method: 'POST',
    body: form,
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

export async function recordLatencyTurn(sample) {
  const res = await fetch(`${API}/latency/turn`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sample),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function getLatencySummary() {
  const res = await fetch(`${API}/latency/summary`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function downloadLatencyCsv() {
  const res = await fetch(`${API}/latency/export.csv`);
  if (!res.ok) throw new Error(await res.text());
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'latency_samples.csv';
  anchor.click();
  URL.revokeObjectURL(url);
}

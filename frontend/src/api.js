const API = import.meta.env.VITE_BACKEND_URL

function withResponseMode(path, responseMode) {
  const url = new URL(`${API}${path}`);
  if (responseMode) url.searchParams.set('response_mode', responseMode);
  return url.toString();
}

export function backendWebSocketUrl(path, responseMode) {
  const base = new URL(API);
  base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';
  base.pathname = path;
  base.search = '';
  if (responseMode) base.searchParams.set('response_mode', responseMode);
  return base.toString();
}

export async function createPipelineSession(responseMode = null) {
  const res = await fetch(withResponseMode('/pipeline/session', responseMode), { method: 'POST' });
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

export async function runPipelineTurnStream(
  sessionId,
  turnId,
  audioBlob,
  audioDurationMs = null,
  handlers = {},
) {
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

  const res = await fetch(`${API}/pipeline/turn/stream`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) throw new Error(await res.text());
  if (!res.body) throw new Error('Streaming response is not available in this browser.');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let metadata = null;
  let done = null;

  const consumeLine = async (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    const event = JSON.parse(trimmed);
    if (event.event === 'metadata') {
      metadata = event;
      await handlers.onMetadata?.(event);
      return;
    }
    if (event.event === 'audio') {
      await handlers.onAudio?.(event);
      return;
    }
    if (event.event === 'done') {
      done = event;
      await handlers.onDone?.(event);
      return;
    }
    if (event.event === 'error') {
      throw new Error(event.detail || 'Streaming pipeline failed');
    }
  };

  while (true) {
    const { value, done: streamDone } = await reader.read();
    if (streamDone) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      await consumeLine(line);
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) await consumeLine(buffer);
  return { metadata, done };
}

export async function createRealtimeSession(responseMode = null) {
  const res = await fetch(withResponseMode('/realtime/session', responseMode), { method: 'POST' });
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

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createPipelineSession,
  deleteSession,
  getLatencySummary,
  recordLatencyTurn,
  runPipelineTurn,
} from '../api';
import LatencyPanel from './LatencyPanel';
import LiveConversationPanel from './LiveConversationPanel';
import DemoDataPanel from './DemoDataPanel';

const BASE_AUDIO_RMS_THRESHOLD = 0.018;
const SILENCE_DURATION_MS = 650;
const MIN_TURN_MS = 260;
const MIN_VOICED_MS = 220;
const MIN_CONSECUTIVE_VOICED_MS = 80;
const MIN_VOICED_RATIO = 0.12;
const PRE_ROLL_MS = 400;
const CAPTURE_BUFFER_SIZE = 2048;

function vadThreshold(noiseFloor) {
  return Math.min(0.055, Math.max(BASE_AUDIO_RMS_THRESHOLD, noiseFloor * 3.2));
}

function hasSufficientSpeech(turn) {
  return turn.voicedMs >= MIN_VOICED_MS
    && turn.maxConsecutiveVoicedMs >= MIN_CONSECUTIVE_VOICED_MS
    && turn.voicedRatio >= MIN_VOICED_RATIO;
}
function duration(start, end) {
  if (start == null || end == null) return null;
  return Math.max(0, Math.round((end - start) * 10) / 10);
}

function base64ToBlob(base64, mediaType) {
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index);
  }
  return new Blob([bytes], { type: mediaType });
}

function encodeWav(chunks, sampleRate) {
  const sampleCount = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + sampleCount * 2);
  const view = new DataView(buffer);

  const writeAscii = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };

  writeAscii(0, 'RIFF');
  view.setUint32(4, 36 + sampleCount * 2, true);
  writeAscii(8, 'WAVE');
  writeAscii(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, 'data');
  view.setUint32(40, sampleCount * 2, true);

  let offset = 44;
  for (const chunk of chunks) {
    for (const value of chunk) {
      const sample = Math.max(-1, Math.min(1, value));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

function createPcmMonitor(stream, onFrame) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) throw new Error('This browser does not support microphone audio processing.');

  const context = new AudioContextClass();
  const source = context.createMediaStreamSource(stream);
  // ScriptProcessor is broadly available in the browsers used for this local
  // demo and gives us raw PCM frames so we can retain speech pre-roll.
  const processor = context.createScriptProcessor(CAPTURE_BUFFER_SIZE, 1, 1);
  const mutedOutput = context.createGain();
  mutedOutput.gain.value = 0;

  processor.onaudioprocess = (event) => {
    const samples = new Float32Array(event.inputBuffer.getChannelData(0));
    let sumSquares = 0;
    for (const value of samples) sumSquares += value * value;
    onFrame(samples, context.sampleRate, Math.sqrt(sumSquares / samples.length), performance.now());
  };

  source.connect(processor);
  processor.connect(mutedOutput);
  mutedOutput.connect(context.destination);
  void context.resume();

  return () => {
    processor.onaudioprocess = null;
    source.disconnect();
    processor.disconnect();
    mutedOutput.disconnect();
    void context.close();
  };
}

function inferToolName(toolResult) {
  const action = toolResult?.action || '';
  if (['identify_caller', 'verify_caller'].includes(action)) return 'customer_identity';
  if (['request_order_id', 'request_ticket_id', 'return_order_status', 'return_ticket_status'].includes(action)) {
    return 'customer_lookup';
  }
  if (['request_callback_time', 'schedule_callback'].includes(action)) return 'support_callback';
  return 'customer_tool';
}

function normalizeToolCalls(result) {
  if (Array.isArray(result.tool_calls) && result.tool_calls.length > 0) return result.tool_calls;
  if (!result.tool_result) return [];

  const response = result.tool_result;
  return [{
    tool_name: inferToolName(response),
    input: {
      purpose: response.purpose ?? null,
      caller_name: response.caller_name || null,
      caller_phone: response.caller_phone || null,
      order_id: response.order_id || null,
      ticket_id: response.ticket_id || null,
      callback_time: response.callback_time || null,
      attempt: response.attempt ?? 0,
    },
    response,
  }];
}

export default function ChainedVoicePanel({
  sessionId,
  resetToken = 0,
  onSessionChange,
  onError,
  controls,
}) {
  const [status, setStatus] = useState('idle');
  const [pipelineMeta, setPipelineMeta] = useState(null);
  const [isUserSpeaking, setIsUserSpeaking] = useState(false);
  const [isAgentSpeaking, setIsAgentSpeaking] = useState(false);
  const [conversationMessages, setConversationMessages] = useState([]);
  const [latencySummary, setLatencySummary] = useState(null);
  const [latencyTurns, setLatencyTurns] = useState([]);
  const [demoDataOpen, setDemoDataOpen] = useState(false);

  const mountedRef = useRef(true);
  const streamRef = useRef(null);
  const monitorCleanupRef = useRef(null);
  const preRollRef = useRef([]);
  const preRollSamplesRef = useRef(0);
  const activeTurnRef = useRef(null);
  const sessionIdRef = useRef('');
  const connectAttemptRef = useRef(0);
  const cleanupPromiseRef = useRef(Promise.resolve());
  const playbackRef = useRef(null);
  const busyRef = useRef(false);
  const statusRef = useRef('idle');
  const agentSpeakingRef = useRef(false);
  const noiseFloorRef = useRef(0.004);

  const refreshLatencySummary = useCallback(async () => {
    try {
      const nextSummary = await getLatencySummary();
      if (mountedRef.current) setLatencySummary(nextSummary);
    } catch (_) {
      // Analytics should not interrupt a call.
    }
  }, []);

  const submitLatency = useCallback((sample) => {
    setLatencyTurns((previous) => [sample, ...previous].slice(0, 12));
    void recordLatencyTurn(sample)
      .then(refreshLatencySummary)
      .catch((error) => onError?.(`Latency sample was not saved: ${error.message}`));
  }, [onError, refreshLatencySummary]);

  const cleanupBackendSession = useCallback((staleSessionId) => {
    if (!staleSessionId) return cleanupPromiseRef.current;
    cleanupPromiseRef.current = cleanupPromiseRef.current
      .catch(() => undefined)
      .then(() => deleteSession(staleSessionId))
      .catch((error) => {
        if (mountedRef.current) {
          onError?.(`Call ended locally, but backend cleanup failed: ${error.message}`);
        }
      });
    return cleanupPromiseRef.current;
  }, [onError]);

  const disconnect = useCallback(() => {
    connectAttemptRef.current += 1;
    const staleSessionId = sessionIdRef.current;
    sessionIdRef.current = '';
    busyRef.current = false;
    preRollRef.current = [];
    preRollSamplesRef.current = 0;
    noiseFloorRef.current = 0.004;
    monitorCleanupRef.current?.();
    monitorCleanupRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    activeTurnRef.current = null;
    if (playbackRef.current) {
      const { audio, audioUrl } = playbackRef.current;
      playbackRef.current = null;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      URL.revokeObjectURL(audioUrl);
    }
    void cleanupBackendSession(staleSessionId);
    onSessionChange?.('');
    setPipelineMeta(null);
    setLatencyTurns([]);
    setConversationMessages([]);
    setDemoDataOpen(false);
    setIsUserSpeaking(false);
    setIsAgentSpeaking(false);
    setStatus('idle');
  }, [cleanupBackendSession, onSessionChange]);

  const finishTurn = useCallback(async (audioBlob, turn) => {
    const activeSessionId = sessionIdRef.current;
    if (!turn || !activeSessionId || audioBlob.size === 0) {
      busyRef.current = false;
      setStatus('listening');
      return;
    }

    setStatus('thinking');
    try {
      const pipelineStartedAt = performance.now();
      const result = await runPipelineTurn(activeSessionId, turn.turnId, audioBlob);
      const pipelineCompletedAt = performance.now();
      if (!mountedRef.current || sessionIdRef.current !== activeSessionId) return;
      const audioBlobOut = base64ToBlob(result.audio_base64, result.audio_media_type);
      const audioUrl = URL.createObjectURL(audioBlobOut);
      const audio = new Audio(audioUrl);
      playbackRef.current = { audio, audioUrl };
      const audioPreparedAt = performance.now();

      setConversationMessages((previous) => [
        ...previous,
        ...(result.transcript ? [{
          id: `${result.turn_id}-user`,
          role: 'user',
          text: result.transcript,
        }] : []),
        {
          id: `${result.turn_id}-assistant`,
          role: 'assistant',
          text: result.response_text,
          tool_calls: normalizeToolCalls(result),
        },
      ]);
      setIsAgentSpeaking(true);
      setStatus('speaking');

      const heardPromise = new Promise((resolve) => {
        let resolved = false;
        const resolveOnce = () => {
          if (resolved) return;
          resolved = true;
          resolve(performance.now());
        };
        audio.addEventListener('playing', resolveOnce, { once: true });
        audio.addEventListener('timeupdate', resolveOnce, { once: true });
      });

      audio.addEventListener('ended', () => {
        URL.revokeObjectURL(audioUrl);
        if (playbackRef.current?.audio !== audio) return;
        playbackRef.current = null;
        setIsAgentSpeaking(false);
        busyRef.current = false;
        if (mountedRef.current) setStatus('listening');
      }, { once: true });
      audio.addEventListener('error', () => {
        URL.revokeObjectURL(audioUrl);
        if (playbackRef.current?.audio !== audio) return;
        playbackRef.current = null;
        setIsAgentSpeaking(false);
        busyRef.current = false;
        setStatus('listening');
        onError?.('Audio playback failed');
      }, { once: true });

      const playbackRequestedAt = performance.now();
      await audio.play();
      const heardAt = await heardPromise;
      const metrics = result.metrics || {};
      const pipelineRoundTripMs = duration(pipelineStartedAt, pipelineCompletedAt) || 0;
      const backendProcessingMs = Number(metrics.backend_processing_ms) || 0;
      const transportSerializationMs = Math.max(0, Math.round((pipelineRoundTripMs - backendProcessingMs) * 10) / 10);
      const responsePrepareMs = duration(pipelineCompletedAt, audioPreparedAt) || 0;
      const playbackStartMs = duration(playbackRequestedAt, heardAt) || 0;
      const audioEncodingMs = Number(turn.audioEncodingMs) || 0;
      const deliveryOverheadMs = Math.round(
        (audioEncodingMs + transportSerializationMs + responsePrepareMs + playbackStartMs) * 10,
      ) / 10;
      submitLatency({
        turn_id: result.turn_id,
        session_id: activeSessionId,
        response_mode: result.response_mode,
        has_tool_call: Boolean(result.has_tool_call),
        transcript: result.transcript || null,
        measurement_source: 'first_audio_event',
        end_of_speech_detection_ms: duration(turn.localSpeechEndAt, turn.turnDetectedAt) || 0,
        stt_ms: metrics.stt_ms,
        llm_ttft_ms: metrics.llm_ttft_ms,
        llm_total_ms: metrics.llm_total_ms,
        tts_ttf_audio_ms: metrics.tts_ttf_audio_ms,
        tts_total_ms: metrics.tts_total_ms,
        backend_processing_ms: metrics.backend_processing_ms,
        backend_other_ms: metrics.backend_other_ms,
        audio_encoding_ms: audioEncodingMs,
        client_pipeline_round_trip_ms: pipelineRoundTripMs,
        transport_serialization_ms: transportSerializationMs,
        response_prepare_ms: responsePrepareMs,
        playback_start_ms: playbackStartMs,
        delivery_overhead_ms: deliveryOverheadMs,
        audio_duration_ms: turn.audioDurationMs,
        voiced_ms: turn.voicedMs,
        voiced_ratio: turn.voicedRatio,
        peak_rms: turn.peakRms,
        vad_threshold: turn.vadThreshold,
        total_to_first_audio_ms: duration(turn.localSpeechEndAt, heardAt) || duration(turn.localSpeechEndAt, pipelineCompletedAt) || 0,
        tool_round_trip_ms: metrics.tool_round_trip_ms,
      });
    } catch (error) {
      if (!mountedRef.current || sessionIdRef.current !== activeSessionId) return;
      busyRef.current = false;
      setIsAgentSpeaking(false);
      setStatus('listening');
      onError?.(error.message || 'Pipeline turn failed');
    }
  }, [onError, submitLatency]);

  const handleAudioFrame = useCallback((samples, sampleRate, rms, now) => {
    const liveStatus = statusRef.current;
    if (liveStatus === 'idle' || liveStatus === 'connecting' || liveStatus === 'thinking' || liveStatus === 'speaking') {
      preRollRef.current = [];
      preRollSamplesRef.current = 0;
      return;
    }
    if (agentSpeakingRef.current || (busyRef.current && !activeTurnRef.current)) return;

    let turn = activeTurnRef.current;
    const frameDurationMs = samples.length / sampleRate * 1000;
    if (!turn) {
      preRollRef.current.push(samples);
      preRollSamplesRef.current += samples.length;
      const maxPreRollSamples = Math.round(sampleRate * PRE_ROLL_MS / 1000);
      while (preRollSamplesRef.current > maxPreRollSamples && preRollRef.current.length > 1) {
        const removed = preRollRef.current.shift();
        preRollSamplesRef.current -= removed.length;
      }

      const threshold = vadThreshold(noiseFloorRef.current);
      if (rms < threshold) {
        noiseFloorRef.current = noiseFloorRef.current * 0.97 + Math.min(rms, 0.03) * 0.03;
        return;
      }

      turn = {
        turnId: crypto.randomUUID(),
        speechStartedAt: now,
        lastVoiceAt: now,
        localSpeechEndAt: null,
        turnDetectedAt: null,
        sampleRate,
        pcmChunks: [...preRollRef.current],
        voicedMs: frameDurationMs,
        consecutiveVoicedMs: frameDurationMs,
        maxConsecutiveVoicedMs: frameDurationMs,
        peakRms: rms,
        vadThreshold: threshold,
      };
      activeTurnRef.current = turn;
      busyRef.current = true;
      setIsUserSpeaking(true);
      setStatus('user_speaking');
      return;
    }

    turn.pcmChunks.push(samples);
    turn.peakRms = Math.max(turn.peakRms, rms);

    if (rms >= turn.vadThreshold) {
      turn.voicedMs += frameDurationMs;
      turn.consecutiveVoicedMs += frameDurationMs;
      turn.maxConsecutiveVoicedMs = Math.max(
        turn.maxConsecutiveVoicedMs,
        turn.consecutiveVoicedMs,
      );
      turn.lastVoiceAt = now;
      return;
    }
    turn.consecutiveVoicedMs = 0;

    const hasMinimumSpeech = now - turn.speechStartedAt >= MIN_TURN_MS;
    if (!hasMinimumSpeech || now - turn.lastVoiceAt < SILENCE_DURATION_MS) return;

    turn.localSpeechEndAt = turn.lastVoiceAt;
    turn.turnDetectedAt = now;
    activeTurnRef.current = null;
    preRollRef.current = [];
    preRollSamplesRef.current = 0;
    setIsUserSpeaking(false);
    const capturedSamples = turn.pcmChunks.reduce((total, chunk) => total + chunk.length, 0);
    turn.audioDurationMs = Math.round(capturedSamples / turn.sampleRate * 10000) / 10;
    turn.voicedMs = Math.round(turn.voicedMs * 10) / 10;
    turn.voicedRatio = Math.round((turn.voicedMs / turn.audioDurationMs) * 1000) / 1000;
    turn.peakRms = Math.round(turn.peakRms * 10000) / 10000;
    turn.vadThreshold = Math.round(turn.vadThreshold * 10000) / 10000;
    if (!hasSufficientSpeech(turn)) {
      busyRef.current = false;
      setStatus('listening');
      return;
    }
    const encodingStartedAt = performance.now();
    const encodedAudio = encodeWav(turn.pcmChunks, turn.sampleRate);
    turn.audioEncodingMs = duration(encodingStartedAt, performance.now()) || 0;
    void finishTurn(encodedAudio, turn);
  }, [finishTurn]);

  const connect = useCallback(async () => {
    const attemptId = connectAttemptRef.current + 1;
    connectAttemptRef.current = attemptId;
    onError?.(null);
    setStatus('connecting');
    setLatencyTurns([]);
    setConversationMessages([]);
    setDemoDataOpen(false);

    try {
      await cleanupPromiseRef.current;
      if (!mountedRef.current || connectAttemptRef.current !== attemptId) return;
      const meta = await createPipelineSession();
      if (!mountedRef.current || connectAttemptRef.current !== attemptId) {
        void cleanupBackendSession(meta.session_id);
        return;
      }
      sessionIdRef.current = meta.session_id;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (
        !mountedRef.current
        || connectAttemptRef.current !== attemptId
        || sessionIdRef.current !== meta.session_id
      ) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      setPipelineMeta(meta);
      onSessionChange?.(meta.session_id);
      monitorCleanupRef.current = createPcmMonitor(stream, handleAudioFrame);
      setStatus('listening');
    } catch (error) {
      disconnect();
      setStatus('error');
      onError?.(error.message || 'Pipeline connection failed');
    }
  }, [cleanupBackendSession, disconnect, handleAudioFrame, onError, onSessionChange]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      disconnect();
    };
  }, [disconnect]);

  useEffect(() => {
    if (resetToken === 0) return;
    disconnect();
  }, [disconnect, resetToken]);

  useEffect(() => {
    void refreshLatencySummary();
  }, [refreshLatencySummary]);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    agentSpeakingRef.current = isAgentSpeaking;
  }, [isAgentSpeaking]);

  const callActive = status !== 'idle' && status !== 'error';
  const activityState = isUserSpeaking
    ? 'listening'
    : isAgentSpeaking
      ? 'speaking'
      : status === 'listening'
        ? 'ready'
        : status;
  const activityCopy = {
    idle: ['Ready when you are', 'Start a call to begin.'],
    connecting: ['Opening the microphone', 'Getting the call ready.'],
    ready: ['Mira is listening', 'Speak naturally.'],
    listening: ['Listening to you', 'Waiting for you to finish.'],
    user_speaking: ['Listening to you', 'Your turn is being recorded.'],
    thinking: ['Mira is working', 'Preparing a reply.'],
    speaking: ['Mira is speaking', 'The response is playing now.'],
    error: ['Call interrupted', 'Start a new call when you are ready.'],
  };
  const [activityTitle, activitySubtitle] = activityCopy[activityState] || activityCopy.idle;

  return (
    <div className="voice-experience">
      <LatencyPanel
        turns={latencyTurns}
        summary={latencySummary}
        activeMode={pipelineMeta?.response_mode || 'llm'}
        status={status === 'listening' || status === 'speaking' ? 'connected' : status}
      />

      <section className="call-stage pipeline-call-stage">
        <aside className="call-rail">
          <div className="call-rail-topline">
            <div className="call-rail-heading">
              <span>Customer success</span>
              <h1>Live call</h1>
            </div>
            <button
              type="button"
              className="demo-records-button"
              onClick={() => setDemoDataOpen(true)}
              aria-label="Open ten demo records"
              title="Demo records"
            >
              <span aria-hidden="true">10</span>
              <strong>Records</strong>
            </button>
          </div>

          {controls}

          <div className="speaker-orbs" aria-label="Call participants">
            <div className={`speaker-orb-card you ${isUserSpeaking ? 'active' : ''}`}>
              <div className="speaker-aura" aria-hidden="true"><i /><i /><span>Y</span></div>
              <strong>You</strong>
              <small>{isUserSpeaking ? 'Speaking' : 'Caller'}</small>
            </div>
            <div className={`speaker-orb-card mira ${isAgentSpeaking ? 'active' : ''}`}>
              <div className="speaker-aura" aria-hidden="true"><i /><i /><span>M</span></div>
              <strong>Mira</strong>
              <small>{isAgentSpeaking ? 'Speaking' : 'Agent'}</small>
            </div>
          </div>

          <div className="rail-activity" aria-live="polite">
            <h2>{activityTitle}</h2>
            <p>{activitySubtitle}</p>
          </div>

          <button
            type="button"
            onClick={callActive ? disconnect : connect}
            className={`rail-call-button ${callActive ? 'hangup' : 'start'}`}
            disabled={status === 'connecting' || status === 'thinking'}
            aria-label={callActive ? 'Disconnect call' : 'Start call with microphone'}
            title={callActive ? 'Disconnect call' : 'Start call'}
          >
            <span aria-hidden="true">
              {callActive ? (
                <svg viewBox="0 0 24 24"><path d="M6.6 10.8c3.6-2.4 7.2-2.4 10.8 0l-1.6 3.1c-.2.4-.7.6-1.1.4l-2-1a1.7 1.7 0 0 0-1.4 0l-2 1c-.4.2-.9 0-1.1-.4l-1.6-3.1Z" /></svg>
              ) : (
                <svg viewBox="0 0 24 24"><path d="M12 15.5a3.5 3.5 0 0 0 3.5-3.5V5a3.5 3.5 0 1 0-7 0v7a3.5 3.5 0 0 0 3.5 3.5Zm6-3.5a1 1 0 1 0-2 0 4 4 0 0 1-8 0 1 1 0 1 0-2 0 6 6 0 0 0 5 5.91V20H8.5a1 1 0 1 0 0 2h7a1 1 0 1 0 0-2H13v-2.09A6 6 0 0 0 18 12Z" /></svg>
              )}
            </span>
          </button>
        </aside>

        <LiveConversationPanel messages={conversationMessages} status={status} />
      </section>

      <DemoDataPanel open={demoDataOpen} onClose={() => setDemoDataOpen(false)} />
    </div>
  );
}

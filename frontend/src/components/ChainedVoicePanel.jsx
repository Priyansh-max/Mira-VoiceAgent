import { useCallback, useEffect, useRef, useState } from 'react';
import {
  backendWebSocketUrl,
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

function base64ToBytes(base64) {
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index);
  }
  return bytes;
}

function pcm16Base64(samples, inputRate, targetRate = 24000) {
  const ratio = inputRate / targetRate;
  const outputLength = Math.max(1, Math.floor(samples.length / ratio));
  const buffer = new ArrayBuffer(outputLength * 2);
  const view = new DataView(buffer);
  for (let index = 0; index < outputLength; index += 1) {
    const sourceIndex = Math.min(samples.length - 1, Math.floor(index * ratio));
    const sample = Math.max(-1, Math.min(1, samples[sourceIndex] || 0));
    view.setInt16(index * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const step = 0x8000;
  for (let index = 0; index < bytes.length; index += step) {
    binary += String.fromCharCode(...bytes.subarray(index, Math.min(index + step, bytes.length)));
  }
  return btoa(binary);
}

function createStreamingAudioPlayer(mediaType = 'audio/mpeg') {
  const MediaSourceClass = window.MediaSource || window.WebKitMediaSource;
  const supported = MediaSourceClass
    && (!MediaSourceClass.isTypeSupported || MediaSourceClass.isTypeSupported(mediaType));
  if (!supported) return null;

  const mediaSource = new MediaSourceClass();
  const audioUrl = URL.createObjectURL(mediaSource);
  const audio = new Audio(audioUrl);
  const queue = [];
  let sourceBuffer = null;
  let ended = false;
  let failed = false;
  let resolveReady;
  let rejectReady;
  const ready = new Promise((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });

  const pump = () => {
    if (!sourceBuffer || sourceBuffer.updating || failed) return;
    if (queue.length > 0) {
      sourceBuffer.appendBuffer(queue.shift());
      return;
    }
    if (ended && mediaSource.readyState === 'open') {
      mediaSource.endOfStream();
    }
  };

  mediaSource.addEventListener('sourceopen', () => {
    try {
      sourceBuffer = mediaSource.addSourceBuffer(mediaType);
      sourceBuffer.mode = 'sequence';
      sourceBuffer.addEventListener('updateend', pump);
      resolveReady();
      pump();
    } catch (error) {
      failed = true;
      rejectReady(error);
    }
  }, { once: true });

  return {
    audio,
    audioUrl,
    async append(bytes) {
      await ready;
      queue.push(bytes);
      pump();
    },
    async end() {
      await ready;
      ended = true;
      pump();
    },
  };
}

function createPcmStreamingAudioPlayer({ sampleRate = 24000, onEnded }) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return null;

  const context = new AudioContextClass({ sampleRate });
  const sources = new Set();
  let nextStartTime = 0;
  let trailingByte = null;
  let ended = false;
  let stopped = false;
  let firstScheduled = false;
  let resolveFirstAudio;
  const firstAudioPromise = new Promise((resolve) => {
    resolveFirstAudio = resolve;
  });

  const finishIfDone = () => {
    if (!ended || sources.size > 0 || stopped) return;
    stopped = true;
    void context.close();
    onEnded?.();
  };

  return {
    firstAudioPromise,
    async append(incomingBytes) {
      if (stopped || !incomingBytes?.length) return;
      let bytes = incomingBytes;
      if (trailingByte !== null) {
        const joined = new Uint8Array(bytes.length + 1);
        joined[0] = trailingByte;
        joined.set(bytes, 1);
        bytes = joined;
        trailingByte = null;
      }
      if (bytes.length % 2 === 1) {
        trailingByte = bytes[bytes.length - 1];
        bytes = bytes.subarray(0, bytes.length - 1);
      }
      if (!bytes.length) return;

      await context.resume();
      const sampleCount = bytes.length / 2;
      const audioBuffer = context.createBuffer(1, sampleCount, sampleRate);
      const channel = audioBuffer.getChannelData(0);
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      for (let index = 0; index < sampleCount; index += 1) {
        channel[index] = view.getInt16(index * 2, true) / 0x8000;
      }

      const source = context.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(context.destination);
      sources.add(source);
      const startAt = Math.max(context.currentTime + 0.025, nextStartTime);
      nextStartTime = startAt + audioBuffer.duration;
      source.onended = () => {
        sources.delete(source);
        source.disconnect();
        finishIfDone();
      };
      source.start(startAt);

      if (!firstScheduled) {
        firstScheduled = true;
        window.setTimeout(
          () => resolveFirstAudio(performance.now()),
          Math.max(0, (startAt - context.currentTime) * 1000),
        );
      }
    },
    end() {
      ended = true;
      finishIfDone();
    },
    stop() {
      if (stopped) return;
      stopped = true;
      for (const source of sources) {
        try { source.stop(); } catch (_) { /* already stopped */ }
      }
      sources.clear();
      void context.close();
    },
  };
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
  transportMode = 'pipeline',
  responseMode = 'speech_directive',
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
  const websocketRef = useRef(null);
  const pendingStreamTurnRef = useRef(null);
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
      const { audio, audioUrl, stop } = playbackRef.current;
      playbackRef.current = null;
      if (stop) stop();
      else if (audio) {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
        URL.revokeObjectURL(audioUrl);
      }
    }
    if (websocketRef.current) {
      websocketRef.current.close();
      websocketRef.current = null;
    }
    pendingStreamTurnRef.current = null;
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
    if (!turn || !activeSessionId || (transportMode === 'pipeline' && (!audioBlob || audioBlob.size === 0))) {
      busyRef.current = false;
      setStatus('listening');
      return;
    }

    setStatus('thinking');
    try {
      const pipelineStartedAt = performance.now();
      let result = null;
      let doneEvent = null;
      let pipelineCompletedAt = null;
      let firstAudioChunkAt = null;
      let audioPreparedAt = null;
      let playbackRequestedAt = null;
      let heardPromise = null;
      let playStarted = false;
      let streamPlayer = null;
      let pcmPlayer = null;
      let incrementalPlayback = false;
      let responseTurnId = turn.turnId;
      let responseTextBuffer = '';
      let responseTextVisible = false;
      const fallbackChunks = [];
      let fallbackMediaType = 'audio/mpeg';

      const revealBufferedResponse = () => {
        if (!mountedRef.current || sessionIdRef.current !== activeSessionId) return;
        responseTextVisible = true;
        setConversationMessages((previous) => previous.map((message) => (
          message.id === `${responseTurnId}-assistant`
            ? { ...message, text: responseTextBuffer || message.text }
            : message
        )));
      };

      const startPlayback = async (audio, audioUrl) => {
        if (playStarted) return;
        playStarted = true;
        playbackRef.current = { audio, audioUrl };
        setIsAgentSpeaking(true);
        setStatus('speaking');
        heardPromise = new Promise((resolve) => {
          let resolved = false;
          const resolveOnce = () => {
            if (resolved) return;
            resolved = true;
            resolve(performance.now());
          };
          audio.addEventListener('playing', resolveOnce, { once: true });
          audio.addEventListener('timeupdate', resolveOnce, { once: true });
        });
        void heardPromise.then(revealBufferedResponse);
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
        playbackRequestedAt = performance.now();
        await audio.play();
      };

      const streamHandlers = {
        onMetadata: async (event) => {
            if (!mountedRef.current || sessionIdRef.current !== activeSessionId) return;
            result = event;
            responseTurnId = event.turn_id;
            responseTextBuffer = event.response_text || responseTextBuffer;
            fallbackMediaType = event.audio_media_type || 'audio/mpeg';
            setConversationMessages((previous) => [
              ...previous,
              ...(event.transcript ? [{
                id: `${event.turn_id}-user`,
                role: 'user',
                text: event.transcript,
              }] : []),
              {
                id: `${event.turn_id}-assistant`,
                role: 'assistant',
                text: responseTextVisible ? responseTextBuffer : '',
                tool_calls: normalizeToolCalls(event),
              },
            ]);
          },
        onResponseDelta: (event) => {
            if (!mountedRef.current || sessionIdRef.current !== activeSessionId) return;
            responseTextBuffer += event.delta || '';
            if (!responseTextVisible) return;
            setConversationMessages((previous) => previous.map((message) => (
              message.id === `${event.turn_id}-assistant`
                ? { ...message, text: responseTextBuffer }
                : message
            )));
          },
        onMetadataUpdate: (event) => {
            if (!mountedRef.current || sessionIdRef.current !== activeSessionId) return;
            result = { ...(result || {}), ...event, metrics: { ...(result?.metrics || {}), ...(event.metrics || {}) } };
            responseTextBuffer = event.response_text || responseTextBuffer;
            setConversationMessages((previous) => previous.map((message) => (
              message.id === `${event.turn_id}-assistant`
                  ? {
                    ...message,
                    text: responseTextVisible ? responseTextBuffer : message.text,
                    tool_calls: normalizeToolCalls(event),
                  }
                : message
            )));
          },
        onAudio: async (event) => {
            if (!mountedRef.current || sessionIdRef.current !== activeSessionId) return;
            const bytes = base64ToBytes(event.audio_base64);
            if (!firstAudioChunkAt) firstAudioChunkAt = performance.now();
            if (fallbackMediaType.startsWith('audio/pcm')) {
              if (!pcmPlayer) {
                pcmPlayer = createPcmStreamingAudioPlayer({
                  sampleRate: 24000,
                  onEnded: () => {
                    if (sessionIdRef.current !== activeSessionId) return;
                    playbackRef.current = null;
                    setIsAgentSpeaking(false);
                    busyRef.current = false;
                    if (mountedRef.current) setStatus('listening');
                  },
                });
                if (!pcmPlayer) throw new Error('This browser cannot play streaming PCM audio.');
                audioPreparedAt = performance.now();
                playbackRequestedAt = performance.now();
                heardPromise = pcmPlayer.firstAudioPromise;
                void heardPromise.then(revealBufferedResponse);
                playbackRef.current = { stop: pcmPlayer.stop };
                setIsAgentSpeaking(true);
                setStatus('speaking');
              }
              incrementalPlayback = true;
              await pcmPlayer.append(bytes);
              return;
            }
            if (!streamPlayer) {
              streamPlayer = createStreamingAudioPlayer(fallbackMediaType);
              audioPreparedAt = performance.now();
            }
            if (streamPlayer) {
              incrementalPlayback = true;
              await streamPlayer.append(bytes);
              await startPlayback(streamPlayer.audio, streamPlayer.audioUrl);
            } else {
              fallbackChunks.push(bytes);
            }
          },
        onDone: async (event) => {
            doneEvent = event;
          },
      };

      if (transportMode === 'pipeline') {
        // Baseline mode deliberately waits for the completed STT -> LLM -> TTS
        // request. This is the comparison point for the persistent WebSocket
        // streaming transport below.
        const event = await runPipelineTurn(
          activeSessionId,
          turn.turnId,
          audioBlob,
          turn.audioDurationMs,
        );
        await streamHandlers.onMetadata(event);
        fallbackChunks.push(base64ToBytes(event.audio_base64));
        await streamHandlers.onDone(event);
      } else if (transportMode === 'streaming') {
        const socket = websocketRef.current;
        if (!socket || socket.readyState !== WebSocket.OPEN) {
          throw new Error('Streaming socket is not connected.');
        }
        await new Promise((resolve, reject) => {
          pendingStreamTurnRef.current = {
            turnId: turn.turnId,
            handlers: streamHandlers,
            resolve,
            reject,
          };
          socket.send(JSON.stringify({
            event: 'speech_end',
            turn_id: turn.turnId,
            audio_duration_ms: turn.audioDurationMs,
          }));
        });
      } else {
        throw new Error(`Unsupported pipeline transport: ${transportMode}`);
      }
      pipelineCompletedAt = performance.now();

      if (!mountedRef.current || sessionIdRef.current !== activeSessionId || !result) return;

      if (pcmPlayer) {
        pcmPlayer.end();
      } else if (streamPlayer) {
        await streamPlayer.end();
      } else if (fallbackChunks.length > 0) {
        const audioBlobOut = new Blob(fallbackChunks, { type: fallbackMediaType });
        const audioUrl = URL.createObjectURL(audioBlobOut);
        const audio = new Audio(audioUrl);
        audioPreparedAt = performance.now();
        await startPlayback(audio, audioUrl);
      }

      const heardAt = heardPromise ? await heardPromise : pipelineCompletedAt;
      const metrics = { ...(result.metrics || {}), ...((doneEvent && doneEvent.metrics) || {}) };
      const pipelineRoundTripMs = duration(pipelineStartedAt, pipelineCompletedAt) || 0;
      const firstAudioBackendMs = [
        metrics.stt_ms,
        metrics.llm_total_ms,
        metrics.tool_round_trip_ms,
        metrics.tts_ttf_audio_ms,
      ].reduce((total, value) => total + (Number(value) || 0), 0);
      const firstAudioElapsedMs = duration(pipelineStartedAt, firstAudioChunkAt || pipelineCompletedAt) || 0;
      const transportSerializationMs = Math.max(
        0,
        Math.round((firstAudioElapsedMs - firstAudioBackendMs) * 10) / 10,
      );
      const responsePrepareMs = duration(firstAudioChunkAt, audioPreparedAt) || 0;
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
        agent_response: result.response_text || null,
        measurement_source: incrementalPlayback
          ? 'streamed_first_audio'
          : 'buffered_complete_audio',
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
        total_to_first_audio_ms: duration(turn.localSpeechEndAt, heardAt) || firstAudioElapsedMs,
        tool_round_trip_ms: metrics.tool_round_trip_ms,
      });
    } catch (error) {
      if (!mountedRef.current || sessionIdRef.current !== activeSessionId) return;
      busyRef.current = false;
      setIsAgentSpeaking(false);
      setStatus('listening');
      onError?.(error.message || 'Pipeline turn failed');
    }
  }, [onError, submitLatency, transportMode]);

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
      if (transportMode === 'streaming') {
        const socket = websocketRef.current;
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ event: 'speech_start', turn_id: turn.turnId }));
          for (const preRollSamples of turn.pcmChunks) {
            socket.send(JSON.stringify({
              event: 'audio_frame',
              turn_id: turn.turnId,
              audio_base64: pcm16Base64(preRollSamples, sampleRate),
            }));
          }
        }
      }
      setIsUserSpeaking(true);
      setStatus('user_speaking');
      return;
    }

    turn.pcmChunks.push(samples);
    if (transportMode === 'streaming') {
      const socket = websocketRef.current;
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({
          event: 'audio_frame',
          turn_id: turn.turnId,
          audio_base64: pcm16Base64(samples, sampleRate),
        }));
      }
    }
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
    let encodedAudio = null;
    if (transportMode === 'pipeline') {
      const encodingStartedAt = performance.now();
      encodedAudio = encodeWav(turn.pcmChunks, turn.sampleRate);
      turn.audioEncodingMs = duration(encodingStartedAt, performance.now()) || 0;
    } else {
      turn.audioEncodingMs = 0;
    }
    void finishTurn(encodedAudio, turn);
  }, [finishTurn, transportMode]);

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
      let meta;
      let streamingSocket = null;
      if (transportMode === 'streaming') {
        const socket = new WebSocket(backendWebSocketUrl('/pipeline/ws', responseMode));
        streamingSocket = socket;
        meta = await new Promise((resolve, reject) => {
          let sessionMeta = null;
          let streamReady = false;
          const timeoutId = window.setTimeout(() => {
            socket.close();
            reject(new Error('Realtime STT did not become ready within 20 seconds'));
          }, 20000);
          socket.onmessage = async (message) => {
            try {
              const event = JSON.parse(message.data);
              if (event.event === 'session') {
                sessionMeta = {
                  session_id: event.session_id,
                  response_mode: event.response_mode,
                  pipeline_mode: event.pipeline_mode,
                };
                return;
              }
              if (event.event === 'stt_ready') {
                window.clearTimeout(timeoutId);
                if (!sessionMeta) {
                  reject(new Error('Streaming STT became ready before session initialization'));
                  return;
                }
                streamReady = true;
                resolve(sessionMeta);
                return;
              }
              const pending = pendingStreamTurnRef.current;
              if (!pending) {
                if (event.event === 'error') {
                  const error = new Error(event.detail || 'Streaming pipeline failed');
                  window.clearTimeout(timeoutId);
                  reject(error);
                }
                return;
              }
              if (event.turn_id && event.turn_id !== pending.turnId) return;
                if (event.event === 'metadata') {
                  await pending.handlers.onMetadata?.(event);
                } else if (event.event === 'transcript_delta') {
                  pending.handlers.onTranscriptDelta?.(event);
                } else if (event.event === 'response_delta') {
                  pending.handlers.onResponseDelta?.(event);
                } else if (event.event === 'metadata_update') {
                  pending.handlers.onMetadataUpdate?.(event);
                } else if (event.event === 'audio') {
                await pending.handlers.onAudio?.(event);
              } else if (event.event === 'done') {
                await pending.handlers.onDone?.(event);
                pendingStreamTurnRef.current = null;
                pending.resolve(event);
              } else if (event.event === 'error') {
                pendingStreamTurnRef.current = null;
                pending.reject(new Error(event.detail || 'Streaming pipeline failed'));
              }
            } catch (error) {
              const pending = pendingStreamTurnRef.current;
              if (pending) {
                pendingStreamTurnRef.current = null;
                pending.reject(error);
              }
            }
          };
          socket.onerror = () => {
            window.clearTimeout(timeoutId);
            reject(new Error('Streaming socket failed to connect'));
          };
          socket.onclose = () => {
            if (!streamReady) {
              window.clearTimeout(timeoutId);
              reject(new Error('Streaming backend closed before realtime STT was ready'));
            }
            const pending = pendingStreamTurnRef.current;
            if (pending) {
              pendingStreamTurnRef.current = null;
              pending.reject(new Error('Streaming socket closed'));
            }
          };
        });
        websocketRef.current = socket;
      } else {
        meta = await createPipelineSession(responseMode);
      }
      if (!mountedRef.current || connectAttemptRef.current !== attemptId) {
        if (transportMode === 'streaming') (streamingSocket || websocketRef.current)?.close();
        else void cleanupBackendSession(meta.session_id);
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
  }, [cleanupBackendSession, disconnect, handleAudioFrame, onError, onSessionChange, responseMode, transportMode]);

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

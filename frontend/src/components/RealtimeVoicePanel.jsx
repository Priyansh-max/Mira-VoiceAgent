import { useCallback, useEffect, useRef, useState } from 'react';
import {
  createRealtimeSession,
  deleteSession,
  executeRealtimeTool,
  getLatencySummary,
  recordLatencyTurn,
} from '../api';
import LatencyPanel from './LatencyPanel';

const OPENAI_REALTIME_URL = 'https://api.openai.com/v1/realtime/calls';
const AUDIO_RMS_THRESHOLD = 0.018;

const AUDIO_DELTA_EVENTS = new Set(['response.output_audio.delta', 'response.audio.delta']);
const AUDIO_DONE_EVENTS = new Set(['response.output_audio.done', 'response.audio.done']);
const AUDIO_TRANSCRIPT_DELTA_EVENTS = new Set([
  'response.output_audio_transcript.delta',
  'response.audio_transcript.delta',
]);
const AUDIO_TRANSCRIPT_DONE_EVENTS = new Set([
  'response.output_audio_transcript.done',
  'response.audio_transcript.done',
]);

function createEnergyMonitor(stream, onEnergy) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return () => {};

  const context = new AudioContextClass();
  const source = context.createMediaStreamSource(stream);
  const analyser = context.createAnalyser();
  analyser.fftSize = 512;
  source.connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  let frameId;

  const sample = () => {
    analyser.getFloatTimeDomainData(samples);
    let sumSquares = 0;
    for (const value of samples) sumSquares += value * value;
    onEnergy(Math.sqrt(sumSquares / samples.length), performance.now());
    frameId = requestAnimationFrame(sample);
  };
  void context.resume();
  frameId = requestAnimationFrame(sample);

  return () => {
    cancelAnimationFrame(frameId);
    source.disconnect();
    analyser.disconnect();
    void context.close();
  };
}

function responseHasFunctionCall(event) {
  return Boolean(event?.response?.output?.some((item) => item?.type === 'function_call'));
}

function duration(start, end) {
  if (start == null || end == null) return null;
  return Math.max(0, Math.round((end - start) * 10) / 10);
}

function summarizeRealtimeEvent(event) {
  const type = event?.type || 'unknown';

  if (type === 'response.function_call_arguments.done') {
    return {
      type: 'realtime_event',
      message: `Realtime requested tool: ${event.name || 'unknown'}`,
      data: {},
    };
  }

  if (type === 'response.output_item.done' && event.item?.type === 'function_call') {
    return {
      type: 'realtime_event',
      message: `Realtime requested tool: ${event.item.name || 'unknown'}`,
      data: {},
    };
  }

  if (type === 'session.created') {
    return {
      type: 'realtime_session',
      message: 'OpenAI realtime session connected',
      data: { id: event.session?.id, model: event.session?.model },
    };
  }

  if (type === 'input_audio_buffer.speech_started') {
    return {
      type: 'realtime_event',
      message: 'User started speaking',
      data: {},
    };
  }

  if (type === 'input_audio_buffer.speech_stopped') {
    return {
      type: 'realtime_event',
      message: 'User stopped speaking',
      data: {},
    };
  }

  if (type === 'conversation.item.input_audio_transcription.completed') {
    return {
      type: 'user_transcript',
      message: 'User transcript received',
      data: { text: event.transcript || '' },
    };
  }

  if (AUDIO_TRANSCRIPT_DONE_EVENTS.has(type) || type === 'response.output_text.done') {
    return {
      type: 'assistant_transcript',
      message: 'Assistant response transcript',
      data: { text: event.transcript || event.text || '' },
    };
  }

  if (type === 'error') {
    return {
      type: 'realtime_error',
      message: 'Realtime error',
      data: { error: event.error || event },
    };
  }

  return {
    type: 'realtime_event',
    message: type,
    data: {},
  };
}

function parseFunctionArgs(rawArgs) {
  if (!rawArgs) return {};
  if (typeof rawArgs === 'object') return rawArgs;
  try {
    return JSON.parse(rawArgs);
  } catch (_) {
    return {};
  }
}

function extractFunctionCall(event) {
  if (event?.type === 'response.function_call_arguments.done') {
    return {
      callId: event.call_id,
      name: event.name,
      arguments: parseFunctionArgs(event.arguments),
    };
  }

  if (event?.type === 'response.output_item.done' && event.item?.type === 'function_call') {
    return {
      callId: event.item.call_id,
      name: event.item.name,
      arguments: parseFunctionArgs(event.item.arguments),
    };
  }

  return null;
}

export default function RealtimeVoicePanel({
  sessionId,
  resetToken = 0,
  onSessionChange,
  onError,
  controls,
  responseMode = 'speech_directive',
}) {
  const [status, setStatus] = useState('idle');
  const [realtimeMeta, setRealtimeMeta] = useState(null);
  const [eventLog, setEventLog] = useState([]);
  const [isUserSpeaking, setIsUserSpeaking] = useState(false);
  const [isAgentSpeaking, setIsAgentSpeaking] = useState(false);
  const [latencySummary, setLatencySummary] = useState(null);
  const [latencyTurns, setLatencyTurns] = useState([]);

  const pcRef = useRef(null);
  const dcRef = useRef(null);
  const streamRef = useRef(null);
  const audioRef = useRef(null);
  const handledCallsRef = useRef(new Set());
  const connectAttemptRef = useRef(0);
  const sessionIdRef = useRef('');
  const cleanupPromiseRef = useRef(Promise.resolve());
  const mountedRef = useRef(true);
  const inputMonitorCleanupRef = useRef(null);
  const outputMonitorCleanupRef = useRef(null);
  const inputLastVoiceAtRef = useRef(null);
  const activeTurnRef = useRef(null);

  const refreshLatencySummary = useCallback(async () => {
    try {
      const nextSummary = await getLatencySummary();
      if (mountedRef.current) setLatencySummary(nextSummary);
    } catch (_) {
      // Metrics should never interrupt the voice conversation.
    }
  }, []);

  const completeTurn = useCallback((appSessionId) => {
    const turn = activeTurnRef.current;
    if (!turn || turn.submitted || !turn.finalResponseDoneAt || !turn.firstAudioAt) return;

    turn.submitted = true;
    const heardAt = turn.playbackStartedAt || turn.firstAudioAt;
    const sample = {
      turn_id: turn.turnId,
      session_id: appSessionId,
      response_mode: turn.responseMode,
      has_tool_call: turn.hasToolCall,
      transcript: turn.transcript || null,
      agent_response: turn.agentResponse || null,
      measurement_source: turn.playbackStartedAt ? 'output_energy' : 'first_audio_event',
      end_of_speech_detection_ms: duration(turn.localSpeechEndAt, turn.turnDetectedAt) || 0,
      stt_ms: duration(turn.turnDetectedAt, turn.transcriptCompletedAt),
      llm_ttft_ms: duration(turn.turnDetectedAt, turn.firstModelTokenAt),
      llm_total_ms: duration(turn.turnDetectedAt, turn.initialResponseDoneAt),
      tts_ttf_audio_ms: duration(turn.finalResponseCreatedAt, turn.firstAudioAt),
      total_to_first_audio_ms: duration(turn.localSpeechEndAt, heardAt) || 0,
      tool_round_trip_ms: duration(turn.toolStartedAt, turn.toolCompletedAt),
    };

    setLatencyTurns((previous) => [sample, ...previous].slice(0, 12));

    void recordLatencyTurn(sample)
      .then(refreshLatencySummary)
      .catch((error) => onError?.(`Latency sample was not saved: ${error.message}`));
  }, [onError, refreshLatencySummary]);

  const pushEvent = useCallback((appSessionId, event) => {
    const traceEvent = {
      ts: Date.now() / 1000,
      session_id: appSessionId,
      ...summarizeRealtimeEvent(event),
    };

    setEventLog((prev) => [...prev.slice(-19), traceEvent]);
  }, []);

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
    dcRef.current?.close();
    dcRef.current = null;

    pcRef.current?.getSenders().forEach((sender) => sender.track?.stop());
    pcRef.current?.close();
    pcRef.current = null;

    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;

    inputMonitorCleanupRef.current?.();
    inputMonitorCleanupRef.current = null;
    outputMonitorCleanupRef.current?.();
    outputMonitorCleanupRef.current = null;
    inputLastVoiceAtRef.current = null;
    activeTurnRef.current = null;

    if (audioRef.current) {
      audioRef.current.srcObject = null;
    }

    void cleanupBackendSession(staleSessionId);
    onSessionChange?.('');
    handledCallsRef.current = new Set();
    setEventLog([]);
    setRealtimeMeta(null);
    setLatencyTurns([]);
    setIsUserSpeaking(false);
    setIsAgentSpeaking(false);
    setStatus('idle');
  }, [cleanupBackendSession, onSessionChange]);

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

  const sendRealtimeEvent = useCallback((event) => {
    if (!dcRef.current || dcRef.current.readyState !== 'open') {
      throw new Error('Realtime data channel is not open');
    }
    dcRef.current.send(JSON.stringify(event));
  }, []);

  const handleFunctionCall = useCallback(async (appSessionId, functionCall) => {
    const { callId, name, arguments: toolArgs } = functionCall;

    try {
      if (activeTurnRef.current) {
        activeTurnRef.current.hasToolCall = true;
        activeTurnRef.current.toolStartedAt = performance.now();
      }
      let toolResponse = await executeRealtimeTool(appSessionId, name, toolArgs);
      const chainedCalls = new Set();
      while (['ready_for_lookup', 'route_to_callback', 'route_to_identity'].includes(toolResponse.action)) {
        const nextTool = toolResponse.action === 'ready_for_lookup'
          ? 'customer_lookup'
          : toolResponse.action === 'route_to_identity'
            ? 'customer_identity'
            : 'support_callback';
        const chainKey = `${nextTool}:${toolResponse.purpose}`;
        if (chainedCalls.has(chainKey)) throw new Error('Tool continuation loop detected');
        chainedCalls.add(chainKey);
        toolResponse = await executeRealtimeTool(appSessionId, nextTool, {
          purpose: toolResponse.purpose,
          caller_name: toolResponse.caller_name || null,
          caller_phone: toolResponse.caller_phone || null,
          order_id: toolResponse.order_id || null,
          ticket_id: toolResponse.ticket_id || null,
          callback_time: toolResponse.callback_time || null,
          attempt: toolResponse.attempt,
        });
      }
      if (!mountedRef.current || sessionIdRef.current !== appSessionId) return;
      if (activeTurnRef.current) {
        activeTurnRef.current.toolCompletedAt = performance.now();
        activeTurnRef.current.responseMode = toolResponse.response_mode;
      }

      const modelToolResponse = toolResponse.response_mode === 'speech_directive'
        ? toolResponse
        : {
            purpose: toolResponse.purpose,
            action: toolResponse.action,
            caller_name: toolResponse.caller_name,
            caller_phone: toolResponse.caller_phone,
            order_id: toolResponse.order_id,
            ticket_id: toolResponse.ticket_id,
            callback_time: toolResponse.callback_time,
            attempt: toolResponse.attempt,
            information: toolResponse.information,
            session_state: toolResponse.session_state,
            response_mode: toolResponse.response_mode,
            backend_tool_ms: toolResponse.backend_tool_ms,
          };

      sendRealtimeEvent({
        type: 'conversation.item.create',
        item: {
          type: 'function_call_output',
          call_id: callId,
          output: JSON.stringify(modelToolResponse),
        },
      });
      if (activeTurnRef.current) activeTurnRef.current.awaitingPostToolResponse = true;
      if (toolResponse.response_mode === 'speech_directive') {
        if (!toolResponse.directive?.response_text) {
          throw new Error(`Terminal tool action ${toolResponse.action} did not return a speech directive`);
        }
        sendRealtimeEvent({
          type: 'response.create',
          response: {
            input: [],
            output_modalities: ['audio'],
            tool_choice: 'none',
            instructions: `Say exactly the following, with no additions or omissions:\n${toolResponse.directive.response_text}`,
          },
        });
      } else {
        sendRealtimeEvent({ type: 'response.create' });
      }
    } catch (error) {
      if (!mountedRef.current || sessionIdRef.current !== appSessionId) return;
      sendRealtimeEvent({
        type: 'conversation.item.create',
        item: {
          type: 'function_call_output',
          call_id: callId,
          output: JSON.stringify({
            tool_name: name,
            tool_result: null,
            policy_outcome: {
              code: 'tool_execution_error',
              safe_facts: { message: error.message || 'Tool execution failed' },
              allowed_next_steps: ['Apologize briefly and ask the user to try again.'],
            },
            session_state: {},
          }),
        },
      });
      sendRealtimeEvent({ type: 'response.create' });
      onError?.(error.message || `Realtime tool ${name} failed`);
    }
  }, [onError, sendRealtimeEvent]);

  const connect = useCallback(async () => {
    const attemptId = connectAttemptRef.current + 1;
    connectAttemptRef.current = attemptId;

    onError?.(null);
    setStatus('connecting');
    setEventLog([]);
    setLatencyTurns([]);

    try {
      await cleanupPromiseRef.current;
      if (!mountedRef.current || connectAttemptRef.current !== attemptId) return;
      const realtime = await createRealtimeSession(responseMode);
      if (!mountedRef.current || connectAttemptRef.current !== attemptId) {
        void cleanupBackendSession(realtime.app_session_id);
        return;
      }
      const appSessionId = realtime.app_session_id;
      sessionIdRef.current = appSessionId;
      onSessionChange?.(appSessionId);
      setRealtimeMeta(realtime);

      const pc = new RTCPeerConnection();
      pcRef.current = pc;

      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'connected') {
          setStatus('connected');
        }
        if (['failed', 'closed', 'disconnected'].includes(pc.connectionState)) {
          setStatus('idle');
        }
      };

      pc.ontrack = (event) => {
        const remoteStream = event.streams[0];
        if (!remoteStream) return;
        if (audioRef.current) {
          audioRef.current.srcObject = remoteStream;
        }
        outputMonitorCleanupRef.current?.();
        outputMonitorCleanupRef.current = createEnergyMonitor(remoteStream, (rms, now) => {
          const turn = activeTurnRef.current;
          if (rms >= AUDIO_RMS_THRESHOLD && turn?.turnDetectedAt && !turn.playbackStartedAt) {
            turn.playbackStartedAt = now;
            if (!turn.firstAudioAt) turn.firstAudioAt = now;
            completeTurn(appSessionId);
          }
        });
      };

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (
        !mountedRef.current
        || connectAttemptRef.current !== attemptId
        || pc.signalingState === 'closed'
      ) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      stream.getTracks().forEach((track) => pc.addTrack(track, stream));
      inputMonitorCleanupRef.current?.();
      inputMonitorCleanupRef.current = createEnergyMonitor(stream, (rms, now) => {
        if (rms >= AUDIO_RMS_THRESHOLD) inputLastVoiceAtRef.current = now;
      });

      const dc = pc.createDataChannel('oai-events');
      dcRef.current = dc;

      dc.addEventListener('open', () => {
        setStatus('connected');
      });

      dc.addEventListener('message', (e) => {
        try {
          const event = JSON.parse(e.data);
          const now = performance.now();
          if (event?.type === 'input_audio_buffer.speech_started') {
            setIsUserSpeaking(true);
            inputLastVoiceAtRef.current = now;
            activeTurnRef.current = {
              turnId: crypto.randomUUID(),
              responseMode: realtime.response_mode || 'llm',
              speechStartedAt: now,
              localSpeechEndAt: null,
              turnDetectedAt: null,
              transcriptCompletedAt: null,
              firstModelTokenAt: null,
              initialResponseDoneAt: null,
              finalResponseCreatedAt: null,
              finalResponseDoneAt: null,
              firstAudioAt: null,
              playbackStartedAt: null,
              agentResponse: '',
              hasToolCall: false,
              submitted: false,
            };
          }
          if (event?.type === 'input_audio_buffer.speech_stopped') {
            setIsUserSpeaking(false);
            if (activeTurnRef.current) {
              activeTurnRef.current.localSpeechEndAt = inputLastVoiceAtRef.current || now;
              activeTurnRef.current.turnDetectedAt = now;
            }
          }
          if (event?.type === 'conversation.item.input_audio_transcription.completed') {
            setIsUserSpeaking(false);
            if (activeTurnRef.current) {
              activeTurnRef.current.transcript = event.transcript || '';
              activeTurnRef.current.transcriptCompletedAt = now;
            }
          }
          if ((AUDIO_TRANSCRIPT_DONE_EVENTS.has(event?.type) || event?.type === 'response.output_text.done') && activeTurnRef.current) {
            activeTurnRef.current.agentResponse = event.transcript || event.text || activeTurnRef.current.agentResponse || '';
          }
          if (
            event?.type === 'response.function_call_arguments.delta'
            || event?.type === 'response.function_call_arguments.done'
            || AUDIO_TRANSCRIPT_DELTA_EVENTS.has(event?.type)
            || event?.type === 'response.output_text.delta'
          ) {
            if (activeTurnRef.current && !activeTurnRef.current.firstModelTokenAt) {
              activeTurnRef.current.firstModelTokenAt = now;
            }
          }
          if (event?.type === 'response.created' && activeTurnRef.current) {
            if (!activeTurnRef.current.initialResponseCreatedAt) {
              activeTurnRef.current.initialResponseCreatedAt = now;
              activeTurnRef.current.finalResponseCreatedAt = now;
            } else if (activeTurnRef.current.awaitingPostToolResponse) {
              activeTurnRef.current.finalResponseCreatedAt = now;
              activeTurnRef.current.awaitingPostToolResponse = false;
            }
          }
          if (AUDIO_DELTA_EVENTS.has(event?.type) && activeTurnRef.current && !activeTurnRef.current.firstAudioAt) {
            activeTurnRef.current.firstAudioAt = now;
          }
          if (AUDIO_DELTA_EVENTS.has(event?.type) || event?.type === 'response.created') {
            setIsAgentSpeaking(true);
          }
          if (
            AUDIO_DONE_EVENTS.has(event?.type)
            || AUDIO_TRANSCRIPT_DONE_EVENTS.has(event?.type)
            || event?.type === 'response.output_text.done'
            || event?.type === 'response.done'
            || event?.type === 'error'
          ) {
            setIsAgentSpeaking(false);
          }
          if (event?.type === 'response.done' && activeTurnRef.current) {
            if (!activeTurnRef.current.initialResponseDoneAt) {
              activeTurnRef.current.initialResponseDoneAt = now;
            }
            if (!responseHasFunctionCall(event)) {
              activeTurnRef.current.finalResponseDoneAt = now;
              completeTurn(appSessionId);
            }
          }
          pushEvent(appSessionId, event);
          const functionCall = extractFunctionCall(event);
          if (
            functionCall?.callId
            && functionCall?.name
            && !handledCallsRef.current.has(functionCall.callId)
          ) {
            handledCallsRef.current.add(functionCall.callId);
            void handleFunctionCall(appSessionId, functionCall);
          }
        } catch (_) {
          // Ignore malformed event payloads.
        }
      });

      if (
        !mountedRef.current
        || connectAttemptRef.current !== attemptId
        || pc.signalingState === 'closed'
      ) {
        return;
      }
      const offer = await pc.createOffer();
      if (
        !mountedRef.current
        || connectAttemptRef.current !== attemptId
        || pc.signalingState === 'closed'
      ) {
        return;
      }
      await pc.setLocalDescription(offer);

      const sdpResponse = await fetch(OPENAI_REALTIME_URL, {
        method: 'POST',
        body: offer.sdp,
        headers: {
          Authorization: `Bearer ${realtime.client_secret}`,
          'Content-Type': 'application/sdp',
        },
      });

      if (!sdpResponse.ok) {
        throw new Error(await sdpResponse.text());
      }

      const answer = {
        type: 'answer',
        sdp: await sdpResponse.text(),
      };
      if (
        !mountedRef.current
        || connectAttemptRef.current !== attemptId
        || pc.signalingState === 'closed'
      ) {
        return;
      }
      await pc.setRemoteDescription(answer);
    } catch (error) {
      if (!mountedRef.current || connectAttemptRef.current !== attemptId) {
        return;
      }
      disconnect();
      setStatus('error');
      onError?.(error.message || 'Realtime connection failed');
    }
  }, [cleanupBackendSession, completeTurn, disconnect, handleFunctionCall, onError, onSessionChange, pushEvent, responseMode]);

  const callActive = status === 'connected' || status === 'connecting';
  const activityState = isUserSpeaking
    ? 'listening'
    : isAgentSpeaking
      ? 'speaking'
      : status === 'connected'
        ? 'ready'
        : status;
  const latestUserTranscript = [...eventLog]
    .reverse()
    .find((event) => event.type === 'user_transcript')?.data?.text;
  const latestAgentTranscript = [...eventLog]
    .reverse()
    .find((event) => event.type === 'assistant_transcript')?.data?.text;

  const activityCopy = {
    idle: ['Ready when you are', 'Start a call to begin the latency run.'],
    connecting: ['Opening the channel', 'Securing a low-latency WebRTC connection…'],
    ready: ['Mira is listening', 'Speak naturally. You can interrupt at any time.'],
    listening: ['Listening to you', 'Detecting the end of your turn in realtime.'],
    speaking: ['Mira is responding', 'Audio is streaming back as it is generated.'],
    error: ['Connection interrupted', 'Start a new call when you are ready.'],
  };
  const [activityTitle, activitySubtitle] = activityCopy[activityState] || activityCopy.idle;

  return (
    <div className="voice-experience">
      <audio ref={audioRef} autoPlay />

      <LatencyPanel
        turns={latencyTurns}
        summary={latencySummary}
        activeMode={realtimeMeta?.response_mode || 'llm'}
        status={status}
      />

      <section className="call-stage">
        <div className="call-stage-header">
          <div>
            <div className="stage-eyebrow">Live customer success call</div>
            <h1>Talk with Mira</h1>
          </div>
          <div className="session-cluster">
            {controls}
            <span className={`connection-pill ${status}`}>
              <span className="connection-dot" />
              {status === 'connected' ? 'Live' : status}
            </span>
            <span className="session-id">
              {sessionId ? `Session ${sessionId.slice(0, 8)}` : 'No active session'}
            </span>
          </div>
        </div>

        <div className="call-center">
          <div className="participant-label caller-label">
            <span className={`participant-signal ${isUserSpeaking ? 'active' : ''}`} />
            <span>
              <strong>You</strong>
              <small>{isUserSpeaking ? 'Speaking' : 'Caller'}</small>
            </span>
          </div>

          <div className={`orb-system ${activityState}`}>
            <div className="orb-ring orb-ring-one" />
            <div className="orb-ring orb-ring-two" />
            <div className="orb-ring orb-ring-three" />
            <div className="orb-track">
              <span className="orb-satellite" />
            </div>
            <div className="call-orb">
              <div className="orb-glow" />
              <div className="voice-bars" aria-hidden="true">
                {[0, 1, 2, 3, 4].map((bar) => <span key={bar} />)}
              </div>
              <span className="orb-monogram">M</span>
            </div>
          </div>

          <div className="participant-label mira-label">
            <span className={`participant-signal mira ${isAgentSpeaking ? 'active' : ''}`} />
            <span>
              <strong>Mira</strong>
              <small>{isAgentSpeaking ? 'Speaking' : 'AI agent'}</small>
            </span>
          </div>
        </div>

        <div className="call-state-copy" aria-live="polite">
          <h2>{activityTitle}</h2>
          <p>{activitySubtitle}</p>
        </div>

        <button
          type="button"
          onClick={callActive ? disconnect : connect}
          className={`call-control ${callActive ? 'hangup' : 'start'}`}
          disabled={status === 'connecting'}
        >
          <span className="call-control-icon" aria-hidden="true">
            {callActive ? (
              <svg viewBox="0 0 24 24"><path d="M6.6 10.8c3.6-2.4 7.2-2.4 10.8 0l-1.6 3.1c-.2.4-.7.6-1.1.4l-2-1a1.7 1.7 0 0 0-1.4 0l-2 1c-.4.2-.9 0-1.1-.4l-1.6-3.1Z" /></svg>
            ) : (
              <svg viewBox="0 0 24 24"><path d="M12 15.5a3.5 3.5 0 0 0 3.5-3.5V5a3.5 3.5 0 1 0-7 0v7a3.5 3.5 0 0 0 3.5 3.5Zm6-3.5a1 1 0 1 0-2 0 4 4 0 0 1-8 0 1 1 0 1 0-2 0 6 6 0 0 0 5 5.91V20H8.5a1 1 0 1 0 0 2h7a1 1 0 1 0 0-2H13v-2.09A6 6 0 0 0 18 12Z" /></svg>
            )}
          </span>
          <span>
            <strong>{status === 'connecting' ? 'Connecting…' : callActive ? 'End call' : 'Start live call'}</strong>
            <small>{callActive ? 'Close the realtime session' : 'Microphone access required'}</small>
          </span>
        </button>

        <div className="conversation-glance">
          <div className="glance-card caller">
            <span className="glance-label">Latest from you</span>
            <p>{latestUserTranscript || 'Your transcript will appear here while you speak.'}</p>
          </div>
          <div className="conversation-flow" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div className="glance-card agent">
            <span className="glance-label">Latest from Mira</span>
            <p>{latestAgentTranscript || 'Mira’s streamed response will appear here.'}</p>
          </div>
        </div>

        <div className="call-footer">
          <span><i className="footer-dot blue" /> Server VAD</span>
          <span><i className="footer-dot cyan" /> Streaming model</span>
          <span><i className="footer-dot violet" /> Live audio</span>
          {realtimeMeta && (
            <span className="model-label">
              {realtimeMeta.realtime_session?.model || 'Realtime model'}
              {' · '}
              {realtimeMeta.realtime_session?.audio?.output?.voice || 'voice'}
            </span>
          )}
        </div>
      </section>
    </div>
  );
}

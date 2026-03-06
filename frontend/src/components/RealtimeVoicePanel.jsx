import { useCallback, useEffect, useRef, useState } from 'react';
import { createRealtimeSession, executeRealtimeTool } from '../api';

const OPENAI_REALTIME_URL = 'https://api.openai.com/v1/realtime/calls';

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

  if (type === 'response.audio_transcript.done' || type === 'response.output_text.done') {
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
  tracePanel = null,
  onSessionChange,
  onTraceEvent,
  onError,
}) {
  const [status, setStatus] = useState('idle');
  const [realtimeMeta, setRealtimeMeta] = useState(null);
  const [eventLog, setEventLog] = useState([]);
  const [isUserSpeaking, setIsUserSpeaking] = useState(false);
  const [isAgentSpeaking, setIsAgentSpeaking] = useState(false);

  const pcRef = useRef(null);
  const dcRef = useRef(null);
  const streamRef = useRef(null);
  const audioRef = useRef(null);
  const handledCallsRef = useRef(new Set());
  const connectAttemptRef = useRef(0);
  const mountedRef = useRef(true);

  const pushEvent = useCallback((appSessionId, event) => {
    const traceEvent = {
      ts: Date.now() / 1000,
      session_id: appSessionId,
      ...summarizeRealtimeEvent(event),
    };

    setEventLog((prev) => [...prev.slice(-19), traceEvent]);
    onTraceEvent?.(traceEvent);
  }, [onTraceEvent]);

  const disconnect = useCallback(() => {
    connectAttemptRef.current += 1;
    dcRef.current?.close();
    dcRef.current = null;

    pcRef.current?.getSenders().forEach((sender) => sender.track?.stop());
    pcRef.current?.close();
    pcRef.current = null;

    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;

    if (audioRef.current) {
      audioRef.current.srcObject = null;
    }

    handledCallsRef.current = new Set();
    setIsUserSpeaking(false);
    setIsAgentSpeaking(false);
    setStatus('idle');
  }, []);

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
    setEventLog([]);
    setRealtimeMeta(null);
    setIsUserSpeaking(false);
    setIsAgentSpeaking(false);
  }, [disconnect, resetToken]);

  const sendRealtimeEvent = useCallback((event) => {
    if (!dcRef.current || dcRef.current.readyState !== 'open') {
      throw new Error('Realtime data channel is not open');
    }
    dcRef.current.send(JSON.stringify(event));
  }, []);

  const handleFunctionCall = useCallback(async (appSessionId, functionCall) => {
    const { callId, name, arguments: toolArgs } = functionCall;

    try {
      const toolResponse = await executeRealtimeTool(appSessionId, name, toolArgs);

      sendRealtimeEvent({
        type: 'conversation.item.create',
        item: {
          type: 'function_call_output',
          call_id: callId,
          output: JSON.stringify(toolResponse),
        },
      });
      sendRealtimeEvent({ type: 'response.create' });
    } catch (error) {
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

    try {
      const realtime = await createRealtimeSession();
      if (!mountedRef.current || connectAttemptRef.current !== attemptId) {
        return;
      }
      const appSessionId = realtime.app_session_id;
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
        if (audioRef.current) {
          audioRef.current.srcObject = event.streams[0];
        }
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

      const dc = pc.createDataChannel('oai-events');
      dcRef.current = dc;

      dc.addEventListener('open', () => {
        setStatus('connected');
      });

      dc.addEventListener('message', (e) => {
        try {
          const event = JSON.parse(e.data);
          if (event?.type === 'input_audio_buffer.speech_started') {
            setIsUserSpeaking(true);
          }
          if (
            event?.type === 'input_audio_buffer.speech_stopped'
            || event?.type === 'conversation.item.input_audio_transcription.completed'
          ) {
            setIsUserSpeaking(false);
          }
          if (event?.type === 'response.audio.delta' || event?.type === 'response.created') {
            setIsAgentSpeaking(true);
          }
          if (
            event?.type === 'response.audio.done'
            || event?.type === 'response.audio_transcript.done'
            || event?.type === 'response.output_text.done'
            || event?.type === 'response.done'
            || event?.type === 'error'
          ) {
            setIsAgentSpeaking(false);
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
  }, [disconnect, handleFunctionCall, onError, onSessionChange, pushEvent]);

  return (
    <div className="grid h-full min-h-0 gap-5 lg:grid-cols-[minmax(0,1.25fr)_minmax(320px,0.88fr)]">
      <audio ref={audioRef} autoPlay />

      <section className="flex min-h-0 min-w-0 flex-col rounded-[28px] border border-white/60 bg-white/38 p-4 shadow-[0_18px_44px_rgba(44,62,68,0.08)] backdrop-blur-2xl sm:p-5">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="text-sm font-semibold text-slate-700">Live voice console</div>
            {realtimeMeta && (
              <div className="mt-2 text-xs text-slate-400">
                Model: {realtimeMeta.realtime_session?.model || 'unknown'} • Voice: {realtimeMeta.realtime_session?.audio?.output?.voice || 'unknown'}
              </div>
            )}
          </div>
          <div className="rounded-full border border-white/70 bg-white/55 px-4 py-2 text-xs font-medium tracking-[0.02em] text-slate-500 shadow-[inset_0_1px_0_rgba(255,255,255,0.7)] backdrop-blur-xl">
            {sessionId ? `Session ${sessionId.slice(0, 8)}...` : 'Preparing session'}
          </div>
        </div>

        <div className="mb-5 grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-3">
          <div className="relative overflow-hidden rounded-[24px] border border-white/70 bg-white/50 px-4 py-5 text-center shadow-[inset_0_1px_0_rgba(255,255,255,0.7)]">
            {isUserSpeaking && <div className="speaker-wave speaker-wave-user" />}
            <div className="relative mx-auto mb-3 flex h-20 w-20 items-center justify-center rounded-full bg-gradient-to-b from-[#f3d8cf] to-[#dbab95] text-sm font-semibold text-[#5f3f35] shadow-[0_12px_24px_rgba(95,63,53,0.12)]">
              Caller
            </div>
            <div className="relative text-base font-semibold text-slate-900">Customer</div>
            <div className="relative mt-1 text-sm text-slate-500">
              {isUserSpeaking ? 'Speaking now' : 'Human side'}
            </div>
          </div>

          <div className="flex justify-center">
            <div className="rounded-full border border-white/70 bg-[rgba(255,255,255,0.46)] px-4 py-2 text-[13px] font-medium text-slate-700 shadow-[0_10px_22px_rgba(15,23,42,0.08)] backdrop-blur-xl">
              Live
            </div>
          </div>

          <div className="relative overflow-hidden rounded-[24px] border border-white/70 bg-white/50 px-4 py-5 text-center shadow-[inset_0_1px_0_rgba(255,255,255,0.7)]">
            {isAgentSpeaking && <div className="speaker-wave speaker-wave-agent" />}
            <div className="relative mx-auto mb-3 flex h-20 w-20 items-center justify-center rounded-full bg-[radial-gradient(circle_at_35%_35%,#b5eff1_0%,#79bfd3_42%,#5f87c7_100%)] text-sm font-semibold text-white shadow-[0_12px_24px_rgba(95,135,199,0.18)]">
              AI
            </div>
            <div className="relative text-base font-semibold text-slate-900">Mira</div>
            <div className="relative mt-1 text-sm text-slate-500">
              {isAgentSpeaking ? 'Responding now' : 'Voice agent'}
            </div>
          </div>
        </div>

        <div className="flex min-h-0 flex-1 flex-col rounded-[24px] border border-white/70 bg-[linear-gradient(180deg,rgba(251,253,253,0.72),rgba(243,248,248,0.56))] p-4 shadow-[0_16px_36px_rgba(44,62,68,0.08)]">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="text-base font-semibold text-slate-900">Realtime Voice</div>
              <div className="mt-1 text-sm text-slate-500">
                Status:
                <span className="ml-2 inline-flex rounded-full bg-slate-900/6 px-2.5 py-1 text-xs font-medium capitalize text-slate-700">
                  {status === 'connected' ? 'Live' : status}
                </span>
                {sessionId ? ` • app session ${sessionId.slice(0, 8)}…` : ''}
              </div>
            </div>
            <button
              type="button"
              onClick={status === 'connected' || status === 'connecting' ? disconnect : connect}
              className={`rounded-full px-4 py-2.5 text-sm font-medium shadow-[0_10px_22px_rgba(15,23,42,0.08)] backdrop-blur-xl transition ${
                status === 'connected' || status === 'connecting'
                  ? 'border border-white/60 bg-white/70 text-slate-700 hover:bg-white'
                  : 'bg-slate-900/88 text-white hover:bg-slate-900'
              }`}
            >
              {status === 'connected' || status === 'connecting' ? 'Disconnect Call' : 'Make call'}
            </button>
          </div>

          <div className="hide-scrollbar min-h-0 flex-1 overflow-y-auto rounded-[20px] border border-white/65 bg-white/42 px-4 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.65)]">
            {eventLog.length === 0 ? (
              <div className="text-sm text-slate-400">Realtime events will appear here after you connect and speak.</div>
            ) : (
              eventLog.map((event, idx) => (
                <div key={`${event.ts}-${idx}`} className="mb-2 last:mb-0 text-sm text-slate-500">
                  <span className="font-medium text-[#2b7a78]">{event.message}</span>
                  {event.data?.text && <span className="text-slate-700"> — {event.data.text}</span>}
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <section className="flex min-h-0 min-w-0 flex-col rounded-[28px] border border-white/60 bg-white/38 p-4 shadow-[0_18px_44px_rgba(44,62,68,0.08)] backdrop-blur-2xl sm:p-5">
        <div className="mb-4">
          <div className="text-sm font-semibold text-slate-700">Live trace</div>
          <div className="mt-1 text-sm text-slate-500">
            Watch transcripts, tool calls, and policy decisions without expanding the whole console.
          </div>
        </div>
        <div className="min-h-0 flex-1">
          {tracePanel}
        </div>
      </section>
    </div>
  );
}

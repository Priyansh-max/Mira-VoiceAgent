import { useEffect, useRef } from 'react';

const TOOL_INPUT_KEYS = [
  'purpose',
  'caller_name',
  'caller_phone',
  'order_id',
  'ticket_id',
  'callback_time',
  'attempt',
];

const TOOL_RESPONSE_KEYS = [
  'purpose',
  'action',
  'caller_name',
  'caller_phone',
  'order_id',
  'ticket_id',
  'callback_time',
  'attempt',
  'directive',
];

function jsonContract(value, keys) {
  return keys.reduce((result, key) => {
    result[key] = value?.[key] ?? null;
    return result;
  }, {});
}

function readableAction(value) {
  return value ? value.replaceAll('_', ' ') : 'completed';
}

function ToolActivity({ toolCall, index }) {
  const request = jsonContract(toolCall.input, TOOL_INPUT_KEYS);
  const response = jsonContract(toolCall.response, TOOL_RESPONSE_KEYS);

  return (
    <details className="tool-json-row" aria-label={`Tool exchange ${index + 1}`}>
      <summary>
        <span className="tool-json-label">Tool</span>
        <code>{toolCall.tool_name || 'customer_tool'}</code>
        <span className="tool-json-action">{readableAction(response.action)}</span>
        <span className="tool-json-chevron" aria-hidden="true" />
      </summary>
      <div className="tool-json-body">
        <section>
          <span>Request</span>
          <pre>{JSON.stringify(request, null, 2)}</pre>
        </section>
        <section>
          <span>Response</span>
          <pre>{JSON.stringify(response, null, 2)}</pre>
        </section>
      </div>
    </details>
  );
}

export default function LiveConversationPanel({ messages = [], status }) {
  const feedRef = useRef(null);

  useEffect(() => {
    const feed = feedRef.current;
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, [messages, status]);

  const isWorking = status === 'thinking';

  return (
    <section className="live-conversation" aria-label="Live call transcript">
      <div className="live-conversation-header">
        <div>
          <span className="conversation-kicker">Live call</span>
          <h2>Transcript</h2>
        </div>
        <span className="conversation-count">
          {messages.length} message{messages.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="conversation-feed" ref={feedRef} aria-live="polite">
        {messages.length === 0 && !isWorking ? (
          <div className="conversation-empty">
            <span className="empty-conversation-mark" aria-hidden="true">
              <i /><i /><i /><i />
            </span>
            <h3>Your live call will appear here</h3>
            <p>Start the call and speak naturally. Transcripts, tool activity, and Mira's replies will stay in order.</p>
          </div>
        ) : (
          messages.map((message) => (
            <div className={`conversation-entry ${message.role}`} key={message.id}>
              {message.role === 'assistant' && message.tool_calls?.map((toolCall, index) => (
                <ToolActivity
                  key={`${message.id}-tool-${index}`}
                  toolCall={toolCall}
                  index={index}
                />
              ))}
              <div className="message-row">
                <span className="message-avatar" aria-hidden="true">
                  {message.role === 'assistant' ? 'M' : 'Y'}
                </span>
                <div className="message-content">
                  <span className="message-speaker">
                    {message.role === 'assistant' ? 'Mira' : 'You'}
                  </span>
                  <p>{message.text}</p>
                </div>
              </div>
            </div>
          ))
        )}

        {isWorking && (
          <div className="conversation-entry assistant pending-entry">
            <div className="message-row">
              <span className="message-avatar" aria-hidden="true">M</span>
              <div className="message-content">
                <span className="message-speaker">Mira</span>
                <span className="thinking-dots" aria-label="Mira is processing the turn">
                  <i /><i /><i />
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

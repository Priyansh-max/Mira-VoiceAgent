import { downloadLatencyCsv } from '../api';

const METRICS = [
  ['end_of_speech_detection_ms', 'End-of-turn', 'VAD'],
  ['stt_ms', 'Transcription', 'STT'],
  ['llm_ttft_ms', 'First model token', 'TTFT'],
  ['llm_total_ms', 'Model response', 'LLM'],
  ['tool_round_trip_ms', 'Local tool chain', 'TOOL'],
  ['tts_ttf_audio_ms', 'First audio', 'TTS'],
  ['tts_total_ms', 'Full synthesis', 'TTSΣ'],
  ['delivery_overhead_ms', 'Browser and delivery', 'I/O'],
];

function formatSeconds(value) {
  if (value == null) return '—';
  return `${(value / 1000).toFixed(value >= 1000 ? 2 : 3)}s`;
}

export default function LatencyPanel({ turns = [], summary, activeMode, status }) {
  const latest = turns[0];
  const streamedPlayback = latest?.measurement_source === 'streamed_first_audio';
  const modeSummary = summary?.by_mode?.[activeMode];
  const benchmarkCount = modeSummary?.count || 0;
  const maxStage = Math.max(
    1,
    ...METRICS.map(([key]) => Number(latest?.[key]) || 0),
  );

  return (
    <aside className="analytics-overlay" aria-label="Live latency analytics">
      <div className="analytics-header">
        <div>
          <div className="analytics-kicker">
            <span className={`analytics-live-dot ${status === 'connected' ? 'active' : ''}`} />
            Live analytics
          </div>
          <h2>Pipeline latency</h2>
        </div>
        <div className="analytics-mode">{activeMode === 'speech_directive' ? 'Directive' : 'LLM'} mode</div>
      </div>

      <div className="latency-hero">
        <div>
          <span>Latest end-to-heard</span>
          <strong>{formatSeconds(latest?.total_to_first_audio_ms)}</strong>
        </div>
        <div className="turn-progress">
          <span>{benchmarkCount}/20</span>
          <small>benchmark turns</small>
        </div>
      </div>

      <div className="pipeline-list">
        {METRICS.map(([key, label, code]) => {
          const value = latest?.[key];
          const width = value == null ? 0 : Math.max(5, (value / maxStage) * 100);
          const displayLabel = key === 'tts_total_ms' && streamedPlayback
            ? 'Full synthesis (background)'
            : label;
          return (
            <div className="pipeline-row" key={key}>
              <span className="pipeline-code">{code}</span>
              <div className="pipeline-stage">
                <div className="pipeline-stage-copy">
                  <span>{displayLabel}</span>
                  <strong>{formatSeconds(value)}</strong>
                </div>
                <div className="pipeline-track">
                  <span style={{ width: `${width}%` }} />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div className="analytics-footer">
        <span>
          {streamedPlayback
            ? 'End-to-heard uses first audio; synthesis continues in the background.'
            : 'Values are measured in-browser per completed turn.'}
        </span>
        <button type="button" onClick={() => void downloadLatencyCsv()}>Export CSV</button>
      </div>
    </aside>
  );
}

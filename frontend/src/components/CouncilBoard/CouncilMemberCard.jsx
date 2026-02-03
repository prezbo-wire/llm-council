import Markdown from '../Markdown';
import './CouncilMemberCard.css';

const STATUS_CONFIG = {
  idle: { label: 'Idle', className: 'status-idle' },
  queued: { label: 'Queued', className: 'status-queued' },
  thinking: { label: 'Thinking', className: 'status-thinking' },
  streaming: { label: 'Streaming', className: 'status-streaming' },
  done: { label: 'Done', className: 'status-done' },
  error: { label: 'Error', className: 'status-error' },
};

export default function CouncilMemberCard({ member }) {
  const { displayName, status, progressText, latencyMs, error } = member;
  const statusConfig = STATUS_CONFIG[status] || STATUS_CONFIG.idle;

  return (
    <div className={`council-member-card ${statusConfig.className}`}>
      <div className="card-header">
        <span className="member-name">{displayName}</span>
        <span className={`status-pill ${statusConfig.className}`}>
          {status === 'streaming' && <span className="pulse-dot"></span>}
          {statusConfig.label}
        </span>
      </div>

      <div className="card-body">
        {status === 'queued' && (
          <div className="waiting-indicator">
            <div className="dots">
              <span></span>
              <span></span>
              <span></span>
            </div>
            <span className="waiting-text">Waiting...</span>
          </div>
        )}

        {(status === 'thinking' || status === 'streaming') && (
          <div className="streaming-content">
            {progressText ? (
              <div className="progress-text markdown-content">
                <Markdown>{progressText}</Markdown>
              </div>
            ) : (
              <div className="thinking-indicator">
                <div className="typing-dots">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
            )}
          </div>
        )}

        {status === 'done' && (
          <div className="done-content">
            <div className="response-text markdown-content">
              <Markdown>{progressText}</Markdown>
            </div>
          </div>
        )}

        {status === 'error' && (
          <div className="error-content">
            <span className="error-icon">!</span>
            <span className="error-message">{error || 'Failed to respond'}</span>
          </div>
        )}
      </div>

      {status === 'done' && latencyMs && (
        <div className="card-footer">
          <span className="metric">
            <span className="metric-label">Latency:</span>
            <span className="metric-value">{(latencyMs / 1000).toFixed(1)}s</span>
          </span>
        </div>
      )}
    </div>
  );
}

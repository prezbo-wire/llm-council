import ReactMarkdown from 'react-markdown';
import './FinalDecisionPanel.css';

export default function FinalDecisionPanel({ decision, onRetry }) {
  const { status, content, latencyMs, error } = decision;

  return (
    <div className="final-decision-panel">
      <div className="panel-header">
        <h3 className="panel-title">Final Decision</h3>
        {status === 'ready' && latencyMs && (
          <span className="latency-badge">{(latencyMs / 1000).toFixed(1)}s</span>
        )}
      </div>

      <div className="panel-body">
        {status === 'empty' && (
          <div className="empty-state">
            <div className="empty-icon">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <p className="empty-text">Final decision will appear here</p>
            <p className="empty-subtext">The Chairman will synthesize all council responses</p>
          </div>
        )}

        {status === 'generating' && (
          <div className="generating-state">
            <div className="spinner-large"></div>
            <p className="generating-text">Chairman is synthesizing...</p>
            <p className="generating-subtext">Analyzing all council responses and rankings</p>
          </div>
        )}

        {status === 'streaming' && (
          <div className="streaming-state">
            <div className="streaming-indicator">
              <span className="pulse-dot-large"></span>
              <span className="streaming-label">Chairman is writing...</span>
            </div>
            <div className="decision-content markdown-content">
              <ReactMarkdown>{content || ''}</ReactMarkdown>
            </div>
          </div>
        )}

        {status === 'ready' && (
          <div className="ready-state">
            <div className="decision-content markdown-content">
              <ReactMarkdown>{content}</ReactMarkdown>
            </div>
          </div>
        )}

        {status === 'error' && (
          <div className="error-state">
            <div className="error-icon">!</div>
            <p className="error-message">{error || 'Failed to generate final decision'}</p>
            {onRetry && (
              <button className="retry-button" onClick={onRetry}>
                Retry
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

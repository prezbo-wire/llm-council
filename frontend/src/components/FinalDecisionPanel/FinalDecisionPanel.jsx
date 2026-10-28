import { useRef } from 'react';
import Markdown from '../Markdown';
import ModelBadge from '../ModelBadge';
import Elapsed from '../Elapsed';
import { useNameOf } from '../../council/CatalogContext.js';
import {
  formatCost,
  formatLatency,
  humanizeError,
  queuedText,
  retryText,
} from '../../council/format.js';
import { exportChairmanPdf } from '../../exportChairmanPdf.js';
import './FinalDecisionPanel.css';

function PendingState({ title, subtitle }) {
  return (
    <div className="generating-state">
      <div className="spinner-large"></div>
      <p className="generating-text">{title}</p>
      {subtitle && <p className="generating-subtext">{subtitle}</p>}
    </div>
  );
}

function pendingCopy(decision) {
  switch (decision.status) {
    case 'queued':
      return { title: `Chairman ${queuedText(decision.position, decision.nodeId).toLowerCase()}`, subtitle: 'Waiting for a free slot' };
    case 'loading':
      return {
        title: (
          <>
            {`Loading the chairman${decision.nodeId ? ` on ${decision.nodeId}` : ''}…`} <Elapsed since={decision.since} />
          </>
        ),
        subtitle: 'Local models load on first use',
      };
    case 'retrying':
      return { title: retryText(decision.attempt, decision.reason), subtitle: 'The chairman call is being retried' };
    case 'thinking':
      return { title: 'Chairman is reasoning...', subtitle: 'Analyzing all council responses and rankings' };
    default:
      return { title: 'Chairman is synthesizing...', subtitle: 'Analyzing all council responses and rankings' };
  }
}

/**
 * Chairman panel for the live run. `decision` is the councilEvents.js `final` slice; `chairman`
 * the council_started chairman. `onRetry` stays unwired in v1 (§I.9).
 */
export default function FinalDecisionPanel({ decision, chairman = null, onRetry }) {
  const { status, content, latencyMs, costUsd, error } = decision;
  const responseRef = useRef(null);
  const nameOf = useNameOf();
  const chairId = decision.model || (chairman && chairman.modelId) || null;
  const chairHint = chairman && chairman.modelId === chairId
    ? { displayName: chairman.displayName, kind: chairman.kind, providerId: chairman.providerId, nodeId: chairman.nodeId }
    : null;
  const hasResponseContent = status === 'streaming' || status === 'ready';
  const latency = status === 'ready' ? formatLatency(latencyMs) : null;
  const cost = status === 'ready' ? formatCost(costUsd) : null;
  const pending = ['generating', 'queued', 'loading', 'retrying', 'thinking'].includes(status);

  const handleExportPdf = () => {
    exportChairmanPdf({
      renderedHtml: responseRef.current?.innerHTML || '',
      fallbackText: content,
      chairmanName: chairId ? nameOf(chairId) : '',
    });
  };

  const exportButton = hasResponseContent && (
    <button className="export-button" onClick={handleExportPdf} type="button">
      Export PDF
    </button>
  );

  return (
    <div className={`final-decision-panel status-${status}`}>
      <div className="panel-header">
        <div className="panel-heading">
          <h3 className="panel-title">Final Decision</h3>
          {chairId && (
            <span className="panel-chair" title={chairId}>
              {nameOf(chairId)} <ModelBadge modelId={chairId} hint={chairHint} />
            </span>
          )}
        </div>
        {(latency || cost) && (
          <span className="latency-badge">{[latency, cost].filter(Boolean).join(' · ')}</span>
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

        {pending && <PendingState {...pendingCopy(decision)} />}

        {status === 'streaming' && (
          <div className="streaming-state">
            <div className="streaming-indicator">
              <span className="pulse-dot-large"></span>
              <span className="streaming-label">Chairman is writing...</span>
            </div>
            {exportButton}
            <div className="decision-content markdown-content" ref={responseRef}>
              <Markdown>{content || ''}</Markdown>
            </div>
            {exportButton}
          </div>
        )}

        {status === 'ready' && (
          <div className="ready-state">
            {exportButton}
            <div className="decision-content markdown-content" ref={responseRef}>
              <Markdown>{content}</Markdown>
            </div>
            {exportButton}
          </div>
        )}

        {status === 'error' && <FinalError error={error} content={content} onRetry={onRetry} />}
      </div>
    </div>
  );
}

function FinalError({ error, content, onRetry }) {
  const { title, detail, code } = humanizeError(error?.code, error?.message || 'Failed to generate final decision');
  return (
    <div className="error-state">
      <div className="error-icon">!</div>
      <p className="error-message">The chairman could not synthesize a final answer</p>
      <p className="error-reason">
        {title}
        {detail && <span className="error-reason-detail">{detail}</span>}
        {code && <span className="error-reason-code">{code}</span>}
      </p>
      {onRetry && (
        <button className="retry-button" type="button" onClick={onRetry}>
          Retry
        </button>
      )}
      {content && (
        <details className="partial-draft">
          <summary>Partial draft before the failure</summary>
          <div className="markdown-content">
            <Markdown>{content}</Markdown>
          </div>
        </details>
      )}
    </div>
  );
}

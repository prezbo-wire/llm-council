import Markdown from '../Markdown';
import ModelBadge from '../ModelBadge';
import Elapsed from '../Elapsed';
import { useModelInfo } from '../../council/CatalogContext.js';
import {
  formatLatency,
  formatUsageFooter,
  humanizeError,
  loadingText,
  queuedText,
  retryText,
} from '../../council/format.js';
import './CouncilMemberCard.css';

const STATUS_CONFIG = {
  idle: { label: 'Idle', className: 'status-idle' },
  queued: { label: 'Queued', className: 'status-queued' },
  loading: { label: 'Loading', className: 'status-loading' },
  started: { label: 'Started', className: 'status-thinking' },
  retrying: { label: 'Retrying', className: 'status-retrying' },
  thinking: { label: 'Thinking', className: 'status-thinking' },
  streaming: { label: 'Streaming', className: 'status-streaming' },
  done: { label: 'Done', className: 'status-done' },
  error: { label: 'Error', className: 'status-error' },
};

function Dots({ className = 'dots' }) {
  return (
    <div className={className}>
      <span></span>
      <span></span>
      <span></span>
    </div>
  );
}

function PendingBody({ member }) {
  const { status, position, nodeId, since, attempt, reason, progressText } = member;
  if (status === 'queued') {
    return (
      <div className="waiting-indicator">
        <Dots />
        <span className="waiting-text">
          {typeof position === 'number' ? queuedText(position, nodeId) : 'Waiting...'}
          {reason === 'memory' && <span className="waiting-subtext"> · waiting for memory</span>}
        </span>
      </div>
    );
  }
  if (status === 'loading') {
    return (
      <div className="waiting-indicator loading-indicator-card">
        <div className="load-spinner" aria-hidden="true"></div>
        <span className="waiting-text">
          {loadingText(nodeId)} <Elapsed since={since} />
        </span>
      </div>
    );
  }
  if (status === 'retrying') {
    return (
      <div className="waiting-indicator retrying-indicator">
        <Dots />
        <span className="waiting-text">{retryText(attempt, reason)}</span>
      </div>
    );
  }
  // started / thinking: waiting for the first content token
  if (progressText) {
    return (
      <div className="progress-text markdown-content">
        <Markdown>{progressText}</Markdown>
      </div>
    );
  }
  return (
    <div className="thinking-indicator">
      <Dots className="typing-dots" />
      {status === 'thinking' && <span className="thinking-label">Reasoning...</span>}
    </div>
  );
}

function ErrorBody({ member }) {
  const { title, detail, code } = humanizeError(member.error?.code, member.error?.message);
  return (
    <>
      <div className="error-content">
        <span className="error-icon">!</span>
        <div className="error-text">
          <span className="error-message">{title}</span>
          {detail && <span className="error-detail">{detail}</span>}
          {code && <span className="error-code">{code}</span>}
        </div>
      </div>
      {member.progressText && (
        <div className="partial-text markdown-content" title="Partial response before the failure">
          <Markdown>{member.progressText}</Markdown>
        </div>
      )}
    </>
  );
}

function CardFooter({ member }) {
  const latency = formatLatency(member.latencyMs);
  const ttft = formatLatency(member.ttftMs);
  const usage = formatUsageFooter({ costUsd: member.costUsd, usage: member.usage });
  if (!latency && !usage) return null;
  const usageTitle = member.usage
    ? `prompt ${member.usage.promptTokens ?? 0} · completion ${member.usage.completionTokens ?? 0} · reasoning ${member.usage.reasoningTokens ?? 0} tokens`
    : undefined;
  return (
    <div className="card-footer">
      {latency && (
        <span className="metric" title={ttft ? `First token after ${ttft}` : undefined}>
          <span className="metric-label">Latency:</span>
          <span className="metric-value">{latency}</span>
        </span>
      )}
      {usage && (
        <span className="metric metric-usage" title={usageTitle}>
          {usage}
        </span>
      )}
    </div>
  );
}

export default function CouncilMemberCard({ member }) {
  const { memberId, status, progressText } = member;
  const hint = { displayName: member.displayName, kind: member.kind, providerId: member.providerId, nodeId: member.nodeId };
  const info = useModelInfo(memberId, hint);
  const statusConfig = STATUS_CONFIG[status] || STATUS_CONFIG.idle;
  const pending = ['queued', 'loading', 'started', 'retrying', 'thinking'].includes(status);

  return (
    <div className={`council-member-card ${statusConfig.className}`} data-member-id={memberId}>
      <div className="card-header">
        <div className="member-heading">
          <span className="member-name" title={memberId}>
            {info.displayName}
          </span>
          <ModelBadge modelId={memberId} hint={hint} />
          {member.canRank === false && <span className="stage1-only-tag">Stage 1 only</span>}
        </div>
        <span className={`status-pill ${statusConfig.className}`}>
          {(status === 'streaming' || status === 'loading') && <span className="pulse-dot"></span>}
          {statusConfig.label}
        </span>
      </div>

      <div className="card-body">
        {pending && <PendingBody member={member} />}

        {status === 'streaming' && (
          <div className="streaming-content">
            <div className="progress-text markdown-content">
              <Markdown>{progressText}</Markdown>
            </div>
          </div>
        )}

        {status === 'done' && (
          <div className="done-content">
            <div className="response-text markdown-content">
              <Markdown>{progressText}</Markdown>
            </div>
          </div>
        )}

        {status === 'error' && <ErrorBody member={member} />}
      </div>

      {status === 'done' && <CardFooter member={member} />}
    </div>
  );
}

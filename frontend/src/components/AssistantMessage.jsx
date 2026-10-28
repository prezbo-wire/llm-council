import Stage1 from './Stage1';
import Stage2 from './Stage2';
import Stage3 from './Stage3';
import { useNameOf } from '../council/CatalogContext.js';
import { snapshotHints } from '../council/models.js';
import { formatCost, formatTokens, humanizeError, isUnfinishedTurn, stage2SkipText, turnStatusBadge } from '../council/format.js';
import './AssistantMessage.css';

const STAGE_LABEL = { 1: 'Stage 1', 2: 'Ranking', 3: 'Chairman', title: 'Title' };

function FailureNotice({ failures, hints }) {
  const nameOf = useNameOf(hints);
  if (!failures || failures.length === 0) return null;
  return (
    <div className="turn-failures" role="note">
      <div className="turn-failures-title">
        {failures.length} call{failures.length > 1 ? 's' : ''} failed in this turn
      </div>
      <ul>
        {failures.map((failure, index) => {
          const { title, detail } = humanizeError(failure.code, failure.message);
          return (
            <li key={`${failure.stage}-${failure.model}-${index}`}>
              <span className="failure-stage">{STAGE_LABEL[failure.stage] || `Stage ${failure.stage}`}</span>
              <span className="failure-model" title={failure.model}>
                {nameOf(failure.model)}
              </span>
              <span className="failure-reason" title={detail || undefined}>
                {title}
              </span>
              {failure.code && <span className="failure-code">{failure.code}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function TurnUsage({ usage, status }) {
  const badge = turnStatusBadge(status);
  if (!usage && !badge) return null;
  const cost = usage ? formatCost(usage.cost_usd) : null;
  const total = usage ? (usage.prompt_tokens ?? 0) + (usage.completion_tokens ?? 0) : 0;
  const parts = [];
  if (cost) parts.push(`Cost ${cost}`);
  if (total > 0) parts.push(`${formatTokens(total)} tok`);
  return (
    <div className="turn-usage">
      {parts.join(' · ')}
      {badge && (
        <span className={`turn-partial turn-status-${status}`} title={badge.title}>
          {badge.text}
        </span>
      )}
    </div>
  );
}

/**
 * A persisted assistant turn. Renders legacy pre-Mese messages (stage1/2/3 only) and the
 * §I.7 additions (council_snapshot, failures, usage, status, stage3.error) alike. A turn that stopped
 * early (`status` cancelled / error) shows what finished, its failures and the Stage-3 error state.
 */
export default function AssistantMessage({ message }) {
  const hints = snapshotHints(message.council_snapshot);
  const stage1 = Array.isArray(message.stage1) ? message.stage1 : [];
  const stage2 = Array.isArray(message.stage2) ? message.stage2 : [];
  const isNewFormat = Boolean(message.council_snapshot);
  const showSkip = isNewFormat && stage2.length === 0 && stage1.length === 1 && !isUnfinishedTurn(message.status);

  return (
    <div className="assistant-message">
      <div className="message-label">LLM Council</div>
      <Stage1 responses={stage1} hints={hints} />
      <FailureNotice failures={Array.isArray(message.failures) ? message.failures : []} hints={hints} />
      {showSkip && <div className="turn-note">{stage2SkipText('fewer_than_two_responses')}</div>}
      <Stage2
        rankings={stage2}
        labelToModel={message.metadata?.label_to_model}
        aggregateRankings={message.metadata?.aggregate_rankings}
        hints={hints}
      />
      {message.stage3 && <Stage3 finalResponse={message.stage3} hints={hints} usage={message.usage} />}
      <TurnUsage usage={message.usage} status={message.status} />
    </div>
  );
}

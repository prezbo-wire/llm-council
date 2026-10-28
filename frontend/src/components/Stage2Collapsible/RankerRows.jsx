import { ModelLabel } from '../ModelBadge';
import Elapsed from '../Elapsed';
import { useNameOf } from '../../council/CatalogContext.js';
import { humanizeError, loadingText, queuedText, retryText } from '../../council/format.js';
import { rankingSummary } from '../../council/rankings.js';

const ROW_TONE = {
  pending: 'pending',
  queued: 'pending',
  loading: 'active',
  ranking: 'active',
  retrying: 'active',
  done: 'done',
  failed: 'failed',
  skipped: 'skipped',
};

function RankerStatus({ ranker, labelToModel, nameOf }) {
  switch (ranker.status) {
    case 'queued':
      return <span>{queuedText(ranker.position, null)}</span>;
    case 'loading':
      return (
        <span>
          {loadingText(null)} <Elapsed since={ranker.since} />
        </span>
      );
    case 'ranking':
      return <span>Ranking...</span>;
    case 'retrying':
      return <span>{retryText(ranker.attempt, ranker.reason)}</span>;
    case 'done': {
      const summary = rankingSummary(ranker.ranking, labelToModel, nameOf);
      return <span title={summary || undefined}>{summary ? `Done · ${summary}` : 'Done'}</span>;
    }
    case 'failed': {
      const { title, detail } = humanizeError(ranker.error?.code, ranker.error?.message);
      return <span title={detail || undefined}>Failed · {title}</span>;
    }
    case 'skipped':
      return <span>{ranker.reason === 'context' ? 'Skipped · context too small for the ranking prompt' : 'Skipped'}</span>;
    default:
      return <span>Waiting</span>;
  }
}

/** Per-ranker rows for Stage 2: queued, loading, ranking, done, failed, skipped (context). */
export default function RankerRows({ rankers, members, labelToModel = null }) {
  const nameOf = useNameOf();
  if (!rankers || rankers.length === 0) return null;
  return (
    <ul className="ranker-rows">
      {rankers.map((ranker) => {
        const m = members ? members[ranker.memberId] : null;
        const hint = m ? { displayName: m.displayName, kind: m.kind, providerId: m.providerId, nodeId: m.nodeId } : null;
        const tone = ROW_TONE[ranker.status] || 'pending';
        return (
          <li key={ranker.memberId} className={`ranker-row ranker-${tone}`}>
            <span className={`ranker-dot ranker-dot-${tone}`} aria-hidden="true"></span>
            <ModelLabel modelId={ranker.memberId} hint={hint} className="ranker-name" />
            <span className="ranker-status">
              <RankerStatus ranker={ranker} labelToModel={labelToModel} nameOf={nameOf} />
            </span>
          </li>
        );
      })}
    </ul>
  );
}

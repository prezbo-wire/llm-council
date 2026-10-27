import CouncilMemberCard from './CouncilMemberCard';
import { ModelLabel } from '../ModelBadge';
import { memberList, runCostUsd } from '../../councilEvents.js';
import { formatCost, formatTokens } from '../../council/format.js';
import './CouncilBoard.css';

function RunSummary({ run }) {
  const cost = formatCost(runCostUsd(run));
  const summary = run.summary;
  const tokens = summary && summary.tokens ? (summary.tokens.prompt ?? 0) + (summary.tokens.completion ?? 0) : null;
  const parts = [];
  if (cost) parts.push(summary ? `Run cost ${cost}` : `So far ${cost}`);
  if (tokens) parts.push(`${formatTokens(tokens)} tok`);
  if (parts.length === 0 && !summary) return null;
  return (
    <span className={`board-summary ${summary && summary.status === 'partial' ? 'partial' : ''}`}>
      {parts.join(' · ')}
      {summary && summary.status === 'partial' && <span className="board-partial">partial</span>}
    </span>
  );
}

export default function CouncilBoard({ run }) {
  const members = memberList(run);
  if (members.length === 0) return null;
  const chair = run.chairman;

  return (
    <div className="council-board">
      <div className="board-header">
        <h3 className="board-title">LLM Council</h3>
        <RunSummary run={run} />
      </div>
      {chair && (
        <div className="board-chair">
          <span className="board-chair-label">Chair</span>
          <ModelLabel
            modelId={chair.modelId}
            hint={{ displayName: chair.displayName, kind: chair.kind, providerId: chair.providerId, nodeId: chair.nodeId }}
          />
        </div>
      )}
      <div className="members-grid">
        {members.map((member) => (
          <CouncilMemberCard key={member.memberId} member={member} />
        ))}
      </div>
    </div>
  );
}

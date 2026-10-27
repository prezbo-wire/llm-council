import { useState } from 'react';
import Markdown from '../Markdown';
import { ModelLabel } from '../ModelBadge';
import RankerRows from './RankerRows';
import { useNameOf } from '../../council/CatalogContext.js';
import { stage2SkipText } from '../../council/format.js';
import { deAnonymizeText } from '../../council/rankings.js';
import './Stage2Collapsible.css';

function ClipboardIcon() {
  return (
    <span className="header-icon">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
      </svg>
    </span>
  );
}

/**
 * Live Stage-2 view: per-ranker rows while ranking runs, then the collapsible aggregate and
 * raw evaluations. `stage2` is the councilEvents.js `stage2` slice; `members` the run's members map.
 */
export default function Stage2Collapsible({ stage2, members, defaultExpanded = false }) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const [activeTab, setActiveTab] = useState(0);
  const nameOf = useNameOf();

  if (!stage2 || stage2.status === 'idle') return null;
  const rankers = stage2.rankerOrder.map((id) => stage2.rankers[id]);

  if (stage2.status === 'running') {
    const done = rankers.filter((r) => r.status === 'done').length;
    return (
      <div className="stage2-collapsible loading">
        <div className="collapsible-header">
          <div className="header-content">
            <ClipboardIcon />
            <span className="header-title">Peer Rankings</span>
            <div className="spinner-small"></div>
            <span className="loading-text">
              Collecting rankings...{rankers.length > 0 && ` ${done}/${rankers.length}`}
            </span>
          </div>
        </div>
        {rankers.length > 0 && (
          <div className="collapsible-content ranker-content">
            <RankerRows rankers={rankers} members={members} />
          </div>
        )}
      </div>
    );
  }

  if (stage2.status === 'skipped') {
    return (
      <div className="stage2-collapsible skipped">
        <div className="collapsible-header static">
          <div className="header-content">
            <ClipboardIcon />
            <span className="header-title">Peer Rankings</span>
            <span className="skip-text">{stage2SkipText(stage2.skippedReason)}</span>
          </div>
        </div>
      </div>
    );
  }

  const rankings = Array.isArray(stage2.rankings) ? stage2.rankings : [];
  const labelToModel = stage2.labelToModel;
  const aggregateRankings = stage2.aggregateRankings;
  const problemRows = rankers.filter((r) => r.status === 'failed' || r.status === 'skipped');
  if (rankings.length === 0 && problemRows.length === 0) return null;
  const tab = Math.min(activeTab, Math.max(0, rankings.length - 1));
  const current = rankings[tab];

  return (
    <div className={`stage2-collapsible ${isExpanded ? 'expanded' : 'collapsed'}`}>
      <button className="collapsible-header" type="button" aria-expanded={isExpanded} onClick={() => setIsExpanded(!isExpanded)}>
        <div className="header-content">
          <ClipboardIcon />
          <span className="header-title">Peer Rankings</span>
          {aggregateRankings && aggregateRankings.length > 0 && (
            <span className="quick-summary">Top: {nameOf(aggregateRankings[0].model)}</span>
          )}
          {problemRows.length > 0 && (
            <span className="quick-summary warn">
              {problemRows.length} ranker{problemRows.length > 1 ? 's' : ''} missing
            </span>
          )}
        </div>
        <span className={`chevron ${isExpanded ? 'up' : 'down'}`}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </span>
      </button>

      {isExpanded && (
        <div className="collapsible-content">
          {problemRows.length > 0 && (
            <div className="ranker-notes">
              <RankerRows rankers={problemRows} members={members} labelToModel={labelToModel} />
            </div>
          )}

          {aggregateRankings && aggregateRankings.length > 0 && (
            <div className="aggregate-rankings-compact">
              <h4>Street Cred (Aggregate)</h4>
              <div className="aggregate-list-compact">
                {aggregateRankings.map((agg, index) => (
                  <div key={agg.model} className="aggregate-item-compact">
                    <span className="rank-position">#{index + 1}</span>
                    <span className="rank-model">{nameOf(agg.model)}</span>
                    <span className="rank-score">{Number(agg.average_rank).toFixed(2)}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {current && (
            <div className="detailed-rankings">
              <h4>Individual Evaluations</h4>
              <div className="tabs">
                {rankings.map((rank, index) => (
                  <button
                    key={rank.model}
                    type="button"
                    className={`tab ${tab === index ? 'active' : ''}`}
                    onClick={() => setActiveTab(index)}
                  >
                    {nameOf(rank.model)}
                  </button>
                ))}
              </div>

              <div className="tab-content">
                <div className="ranking-model">
                  <ModelLabel modelId={current.model} />
                </div>
                <div className="ranking-content markdown-content">
                  <Markdown>{deAnonymizeText(current.ranking, labelToModel, nameOf)}</Markdown>
                </div>

                {Array.isArray(current.parsed_ranking) && current.parsed_ranking.length > 0 && (
                  <div className="parsed-ranking">
                    <strong>Extracted Ranking:</strong>
                    <ol>
                      {current.parsed_ranking.map((label, i) => (
                        <li key={i}>{labelToModel && labelToModel[label] ? nameOf(labelToModel[label]) : label}</li>
                      ))}
                    </ol>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

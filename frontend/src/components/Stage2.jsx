import { useState } from 'react';
import Markdown from './Markdown';
import { ModelLabel } from './ModelBadge';
import { useNameOf } from '../council/CatalogContext.js';
import { deAnonymizeText } from '../council/rankings.js';
import './Stage2.css';

/** Persisted Stage-2 rankings. De-anonymisation happens client-side for readability only. */
export default function Stage2({ rankings, labelToModel, aggregateRankings, hints = null }) {
  const [activeTab, setActiveTab] = useState(0);
  const nameOf = useNameOf(hints);

  if (!rankings || rankings.length === 0) {
    return null;
  }
  const tab = Math.min(activeTab, rankings.length - 1);
  const current = rankings[tab];

  return (
    <div className="stage stage2">
      <h3 className="stage-title">Stage 2: Peer Rankings</h3>

      <h4>Raw Evaluations</h4>
      <p className="stage-description">
        Each model evaluated all responses (anonymized as Response A, B, C, etc.) and provided rankings.
        Below, model names are shown in <strong>bold</strong> for readability, but the original evaluation used anonymous labels.
      </p>

      <div className="tabs">
        {rankings.map((rank, index) => (
          <button
            key={index}
            type="button"
            className={`tab ${tab === index ? 'active' : ''}`}
            onClick={() => setActiveTab(index)}
            title={rank.model}
          >
            {nameOf(rank.model)}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="ranking-model">
          <ModelLabel modelId={current.model} hint={hints ? hints.get(current.model) : null} />
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

      {aggregateRankings && aggregateRankings.length > 0 && (
        <div className="aggregate-rankings">
          <h4>Aggregate Rankings (Street Cred)</h4>
          <p className="stage-description">
            Combined results across all peer evaluations (lower score is better):
          </p>
          <div className="aggregate-list">
            {aggregateRankings.map((agg, index) => (
              <div key={index} className="aggregate-item">
                <span className="rank-position">#{index + 1}</span>
                <span className="rank-model">{nameOf(agg.model)}</span>
                <span className="rank-score">Avg: {Number(agg.average_rank).toFixed(2)}</span>
                <span className="rank-count">({agg.rankings_count} votes)</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

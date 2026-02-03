import { useState } from 'react';
import Markdown from '../Markdown';
import './Stage2Collapsible.css';

function deAnonymizeText(text, labelToModel) {
  if (!labelToModel) return text;

  let result = text;
  Object.entries(labelToModel).forEach(([label, model]) => {
    const modelShortName = model.split('/')[1] || model;
    result = result.replace(new RegExp(label, 'g'), `**${modelShortName}**`);
  });
  return result;
}

export default function Stage2Collapsible({
  rankings,
  labelToModel,
  aggregateRankings,
  isLoading,
  defaultExpanded = false,
}) {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);
  const [activeTab, setActiveTab] = useState(0);

  if (isLoading) {
    return (
      <div className="stage2-collapsible loading">
        <div className="collapsible-header">
          <div className="header-content">
            <span className="header-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
            </span>
            <span className="header-title">Peer Rankings</span>
            <div className="spinner-small"></div>
            <span className="loading-text">Collecting rankings...</span>
          </div>
        </div>
      </div>
    );
  }

  if (!rankings || rankings.length === 0) {
    return null;
  }

  return (
    <div className={`stage2-collapsible ${isExpanded ? 'expanded' : 'collapsed'}`}>
      <button
        className="collapsible-header"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="header-content">
          <span className="header-icon">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
            </svg>
          </span>
          <span className="header-title">Peer Rankings</span>
          {aggregateRankings && aggregateRankings.length > 0 && (
            <span className="quick-summary">
              Top: {aggregateRankings[0]?.model.split('/')[1] || aggregateRankings[0]?.model}
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
          {/* Aggregate Rankings Summary */}
          {aggregateRankings && aggregateRankings.length > 0 && (
            <div className="aggregate-rankings-compact">
              <h4>Street Cred (Aggregate)</h4>
              <div className="aggregate-list-compact">
                {aggregateRankings.map((agg, index) => (
                  <div key={index} className="aggregate-item-compact">
                    <span className="rank-position">#{index + 1}</span>
                    <span className="rank-model">
                      {agg.model.split('/')[1] || agg.model}
                    </span>
                    <span className="rank-score">
                      {agg.average_rank.toFixed(2)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Detailed Rankings Tabs */}
          <div className="detailed-rankings">
            <h4>Individual Evaluations</h4>
            <div className="tabs">
              {rankings.map((rank, index) => (
                <button
                  key={index}
                  className={`tab ${activeTab === index ? 'active' : ''}`}
                  onClick={() => setActiveTab(index)}
                >
                  {rank.model.split('/')[1] || rank.model}
                </button>
              ))}
            </div>

            <div className="tab-content">
              <div className="ranking-model">
                {rankings[activeTab].model}
              </div>
              <div className="ranking-content markdown-content">
                <Markdown>
                  {deAnonymizeText(rankings[activeTab].ranking, labelToModel)}
                </Markdown>
              </div>

              {rankings[activeTab].parsed_ranking &&
               rankings[activeTab].parsed_ranking.length > 0 && (
                <div className="parsed-ranking">
                  <strong>Extracted Ranking:</strong>
                  <ol>
                    {rankings[activeTab].parsed_ranking.map((label, i) => (
                      <li key={i}>
                        {labelToModel && labelToModel[label]
                          ? labelToModel[label].split('/')[1] || labelToModel[label]
                          : label}
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

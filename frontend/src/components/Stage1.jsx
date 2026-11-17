import { useState } from 'react';
import Markdown from './Markdown';
import { ModelLabel } from './ModelBadge';
import { useNameOf, usePlanOf } from '../council/CatalogContext.js';
import { formatCost, formatLatency } from '../council/format.js';
import './Stage1.css';

/** Persisted Stage-1 responses (legacy and §I.7 shapes alike). `hints` come from council_snapshot. */
export default function Stage1({ responses, hints = null }) {
  const [activeTab, setActiveTab] = useState(0);
  const nameOf = useNameOf(hints);
  const { isPlan } = usePlanOf(hints);

  if (!responses || responses.length === 0) {
    return null;
  }
  const tab = Math.min(activeTab, responses.length - 1);
  const current = responses[tab];
  const cost = formatCost(current.cost_usd, { plan: isPlan(current.model) });
  const meta = [formatLatency(current.latency_ms), cost].filter(Boolean).join(' · ');

  return (
    <div className="stage stage1">
      <h3 className="stage-title">Stage 1: Individual Responses</h3>

      <div className="tabs">
        {responses.map((resp, index) => (
          <button
            key={index}
            type="button"
            className={`tab ${tab === index ? 'active' : ''}`}
            onClick={() => setActiveTab(index)}
            title={resp.model}
          >
            {nameOf(resp.model)}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="model-name">
          <ModelLabel modelId={current.model} hint={hints ? hints.get(current.model) : null} />
          <span className="model-id">{current.model}</span>
          {meta && <span className="model-meta">{meta}</span>}
        </div>
        <div className="response-text markdown-content">
          <Markdown>{current.response}</Markdown>
        </div>
      </div>
    </div>
  );
}

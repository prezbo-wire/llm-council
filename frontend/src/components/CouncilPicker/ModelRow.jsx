import ModelBadge from '../ModelBadge';
import { formatModelPrice, unavailableText } from '../../council/format.js';
import { loadDotFor, residencyHintFor } from '../../council/models.js';
import './ModelRow.css';

/**
 * One catalog model in the picker: member checkbox, name, badge, price per 1M, load dot,
 * "Stage 1 only" (can_rank=false), residency pills (Resident, On demand, Exclusive, Slow, CPU offload),
 * unavailable reason and the chair toggle (can_chair only). `cpuOffload` is the plan's `cpu_offload`
 * warning message for this model, if any.
 */
export default function ModelRow({
  model,
  isMember,
  isChair,
  memberLimitReached,
  adminUrl,
  cpuOffload = null,
  onToggleMember,
  onToggleChair,
}) {
  const price = formatModelPrice(model);
  const dot = loadDotFor(model);
  const residency = residencyHintFor(model, { cpuOffload });
  const unavailable = model.available ? null : unavailableText(model.unavailable_reason);
  const checkboxId = `council-member-${model.id}`;
  const memberDisabled = !isMember && memberLimitReached;

  return (
    <li className={`model-row ${model.available ? '' : 'unavailable'} ${isMember ? 'selected' : ''}`.trim()}>
      <input
        id={checkboxId}
        type="checkbox"
        className="model-row-check"
        checked={isMember}
        disabled={memberDisabled}
        onChange={() => onToggleMember(model.id)}
        title={memberDisabled ? 'A council has at most 8 members' : undefined}
      />
      <div className="model-row-main">
        <label htmlFor={checkboxId} className="model-row-name" title={model.id}>
          {model.name || model.id}
        </label>
        <ModelBadge modelId={model.id} />
        {!model.can_rank && (
          <span className="stage1-only" title="Answers in Stage 1 but does not rank peers (can_rank is off)">
            Stage 1 only
          </span>
        )}
        {residency.map((pill) => (
          <span key={pill.tone} className={`residency-pill pill-${pill.tone}`} title={pill.title}>
            {pill.label}
          </span>
        ))}
        {unavailable && (
          <span className="model-row-unavailable">
            {unavailable.admin && adminUrl ? (
              <a href={adminUrl} target="_blank" rel="noopener noreferrer">
                {unavailable.text} ↗
              </a>
            ) : (
              unavailable.text
            )}
          </span>
        )}
      </div>
      <span className={`model-row-price price-${price.tone}`} title={price.title}>
        {price.text}
      </span>
      <span className="model-row-load" title={dot ? dot.label : undefined}>
        {dot && <span className={`load-dot load-${dot.tone}`} aria-label={dot.label}></span>}
      </span>
      <button
        type="button"
        className={`chair-toggle ${isChair ? 'active' : ''}`}
        aria-pressed={isChair}
        disabled={!model.can_chair && !isChair}
        onClick={() => onToggleChair(model.id)}
        title={model.can_chair ? (isChair ? 'Chairman (click to clear)' : 'Make chairman') : 'This model cannot chair'}
      >
        Chair
      </button>
    </li>
  );
}

import { useModelInfo } from '../council/CatalogContext.js';
import { badgeFor } from '../council/models.js';
import './ModelBadge.css';

/**
 * `Cloud · anthropic` (blue) / `Local · spark` (green) / `FT · v3` (purple) / `Legacy` (grey).
 * Renders nothing while the model cannot be classified yet (catalog loading, no hint).
 */
export default function ModelBadge({ modelId, hint = null }) {
  const info = useModelInfo(modelId, hint);
  const badge = badgeFor(info);
  if (!badge) return null;
  return (
    <span className={`model-badge model-badge-${badge.variant}`} title={badge.title}>
      {badge.label}
    </span>
  );
}

/** Display name plus badge; replaces the old `id.split('/')[1]` labels. */
export function ModelLabel({ modelId, hint = null, showBadge = true, className = '' }) {
  const info = useModelInfo(modelId, hint);
  return (
    <span className={`model-label ${className}`.trim()} title={modelId}>
      <span className="model-label-name">{info.displayName}</span>
      {showBadge && <ModelBadge modelId={modelId} hint={hint} />}
    </span>
  );
}

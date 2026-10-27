import { useRef, useState } from 'react';
import ModelRow from './ModelRow';
import PresetBar from './PresetBar';
import { useCatalog, useNameOf } from '../../council/CatalogContext.js';
import {
  MAX_MEMBERS,
  draftFromPreset,
  pickerShouldCommit,
  toggleChairman,
  toggleMember,
  withPresetMatch,
} from '../../council/draft.js';
import { describeModel, badgeFor, matchesFilter, matchesSearch, sortForPicker } from '../../council/models.js';
import { usePlanWarnings } from '../../hooks/usePlanWarnings.js';
import './CouncilPicker.css';

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'local', label: 'Local' },
  { id: 'cloud', label: 'Cloud' },
];

function MemberChip({ id, flagged }) {
  const { index } = useCatalog();
  const info = describeModel(id, { index });
  const badge = badgeFor(info);
  return (
    <span className={`member-chip ${badge ? `chip-${badge.variant}` : ''} ${flagged ? 'flagged' : ''}`.trim()} title={`${id}${badge ? ` · ${badge.label}` : ''}`}>
      <span className="chip-dot" aria-hidden="true"></span>
      {info.displayName}
      {flagged && <span className="chip-flag" aria-label="unavailable">!</span>}
    </span>
  );
}

function CatalogStatus({ catalog, onRetry }) {
  if (catalog.status === 'loading') return <div className="picker-empty">Loading models from Mese…</div>;
  if (catalog.status === 'error') {
    return (
      <div className="picker-empty error">
        Can&apos;t load the Mese catalog{catalog.error && catalog.error.message ? `: ${catalog.error.message}` : ''}.{' '}
        {onRetry && (
          <button type="button" className="link-button" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    );
  }
  return null;
}

/**
 * Per-conversation council picker (§I.9). Collapsed: member chips, chair, "Change", and a note that
 * changes apply to the next question. Expanded: presets, All/Local/Cloud filter, search and rows.
 */
export default function CouncilPicker({
  catalog,
  presets,
  draft,
  validation,
  disabled = false,
  onDraftChange,
  onSavePreset,
  onDeletePreset,
  onPlan,
  onCommit,
  onRetryCatalog,
}) {
  const [expanded, setExpanded] = useState(false);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  // What the picker showed when it was opened, and whether the user changed anything since (LEAD-1).
  const session = useRef({ openedWith: null, edited: false });
  const { adminUrl } = useCatalog();
  const nameOf = useNameOf();
  const plan = usePlanWarnings(
    onPlan,
    draft,
    Boolean(validation.valid && catalog.status === 'ready' && !disabled),
    validation.unavailable,
  );

  const members = draft ? draft.members : [];
  const chairman = draft ? draft.chairman : null;
  const flagged = new Set([...validation.unavailable, ...validation.unknown]);
  const presetItems = presets.items || [];

  const edit = (next) => {
    session.current.edited = true;
    onDraftChange(next);
  };
  const update = (next) => edit(withPresetMatch(next, presetItems));
  const open = () => {
    session.current = { openedWith: draft, edited: false };
    setExpanded(true);
  };
  /** Saves only a selection the user actually changed; opening and closing the picker never writes. */
  const close = () => {
    setExpanded(false);
    const { openedWith, edited } = session.current;
    if (onCommit && pickerShouldCommit({ edited, openedWith, draft, valid: validation.valid })) onCommit(draft);
  };

  const visibleModels = sortForPicker(catalog.models || []).filter(
    (model) => matchesFilter(model, filter) && matchesSearch(model, query),
  );

  const notes = (
    <div className="picker-notes" aria-live="polite">
      {validation.problems.length > 0 ? (
        <span className="picker-problem">{validation.problems.join(' · ')}</span>
      ) : (
        <span className="picker-note">Changes apply to the next question.</span>
      )}
      {validation.unavailable.length > 0 && (
        <span className="picker-warning">
          Unavailable now: {validation.unavailable.map(nameOf).join(', ')} (fails fast at run time)
        </span>
      )}
      {plan.warnings.map((warning) => (
        <span key={warning.message} className={`picker-warning plan-${warning.code}`}>
          {warning.message}
        </span>
      ))}
    </div>
  );

  if (!expanded) {
    return (
      <div className={`council-picker collapsed ${disabled ? 'disabled' : ''}`}>
        <div className="picker-summary">
          <span className="picker-label">Council</span>
          <div className="member-chips">
            {members.length === 0 && <span className="picker-placeholder">No members picked</span>}
            {members.map((id) => (
              <MemberChip key={id} id={id} flagged={flagged.has(id)} />
            ))}
          </div>
          <span className="picker-chair">
            Chair: <strong>{chairman ? nameOf(chairman) : '—'}</strong>
          </span>
          <button type="button" className="picker-change" disabled={disabled} onClick={open}>
            Change
          </button>
        </div>
        {notes}
      </div>
    );
  }

  const memberLimitReached = members.length >= MAX_MEMBERS;

  return (
    <div
      className="council-picker expanded"
      onKeyDown={(e) => {
        if (e.key === 'Escape') close();
      }}
    >
      <PresetBar
        presets={presets}
        activePresetId={draft ? draft.presetId : null}
        canSave={validation.valid}
        onApply={(preset) => edit(draftFromPreset(preset))}
        onSave={(name) => {
          session.current.edited = true; // saving tags the selection with the new preset id
          return onSavePreset(name, draft);
        }}
        onDelete={onDeletePreset}
      />

      <div className="picker-controls">
        <div className="filter-group" role="group" aria-label="Filter models">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`filter-button ${filter === f.id ? 'active' : ''}`}
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <input
          type="search"
          className="picker-search"
          placeholder="Search models…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search models"
        />
        <span className="picker-count">
          {members.length}/{MAX_MEMBERS} members · Chair: {chairman ? nameOf(chairman) : '—'}
        </span>
        <button type="button" className="picker-done" onClick={close}>
          Done
        </button>
      </div>

      <CatalogStatus catalog={catalog} onRetry={onRetryCatalog} />
      {catalog.status === 'ready' && (
        <ul className="model-list">
          {visibleModels.length === 0 && <li className="picker-empty">No models match.</li>}
          {visibleModels.map((model) => (
            <ModelRow
              key={model.id}
              model={model}
              isMember={members.includes(model.id)}
              isChair={chairman === model.id}
              memberLimitReached={memberLimitReached}
              adminUrl={adminUrl}
              onToggleMember={(id) => update(toggleMember(draft, id))}
              onToggleChair={(id) => update(toggleChairman(draft, id))}
            />
          ))}
        </ul>
      )}
      {notes}
    </div>
  );
}

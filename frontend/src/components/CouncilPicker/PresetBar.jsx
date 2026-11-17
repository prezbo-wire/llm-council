import { Fragment, useState } from 'react';
import { useNameOf } from '../../council/CatalogContext.js';
import { LAST_USED_PRESET_ID, lastUsedChipState } from '../../council/draft.js';
import './PresetBar.css';

/** "Nov 17, 2026, 1:00 PM" in the viewer's locale, or null for a missing or bad timestamp. */
function localTime(iso) {
  const date = iso ? new Date(iso) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : null;
}

/** `lastUsed` (Last used chip only): `{reason, nameOf, sourceName}` adds why it is disabled, the chair, when and from which preset. */
function presetTitle(preset, lastUsed = null) {
  const lines = [];
  if (lastUsed && lastUsed.reason) lines.push(lastUsed.reason);
  if (preset.description) lines.push(preset.description);
  lines.push(`${preset.members.length} member${preset.members.length === 1 ? '' : 's'}`);
  if (lastUsed && preset.chairman) lines.push(`Chair: ${lastUsed.nameOf(preset.chairman)}`);
  if (!preset.chairman) lines.push('No chairman available: pick one after applying');
  if (lastUsed) {
    const sent = localTime(preset.updated_at);
    if (sent) lines.push(`Sent ${sent}`);
    if (lastUsed.sourceName) lines.push(`From preset ${lastUsed.sourceName}`);
  }
  if (preset.unavailable && preset.unavailable.length) lines.push(`Unavailable now: ${preset.unavailable.join(', ')}`);
  return lines.join('\n');
}

/**
 * One-click preset chips (Last used first, then built-in selectors and user presets), "Save as preset…"
 * and delete for user presets. Applying a preset with unavailable members still applies it; the picker
 * flags those members. Last used is never "active" (no draft carries its id); it is disabled before any
 * send and while it already is the current council.
 */
export default function PresetBar({ presets, draft = null, activePresetId, canSave, onApply, onSave, onDelete }) {
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const nameOf = useNameOf();

  const items = presets.items || [];

  const submit = async (event) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onSave(trimmed);
      setNaming(false);
      setName('');
    } catch (err) {
      setError(err && err.message ? err.message : 'Failed to save the preset');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (preset) => {
    if (busy) return;
    if (typeof window !== 'undefined' && !window.confirm(`Delete the preset "${preset.name}"?`)) return;
    setBusy(true);
    setError(null);
    try {
      await onDelete(preset);
    } catch (err) {
      setError(err && err.message ? err.message : 'Failed to delete the preset');
    } finally {
      setBusy(false);
    }
  };

  /** Disabled state and tooltip details of the Last used chip. */
  const lastUsedInfo = (preset) => {
    const source = preset.source_preset_id ? items.find((p) => p.id === preset.source_preset_id && p.id !== preset.id) : null;
    return { ...lastUsedChipState(preset, draft), nameOf, sourceName: source ? source.name : null };
  };

  return (
    <div className="preset-bar">
      <span className="preset-bar-label">Presets</span>
      {presets.status === 'loading' && <span className="preset-hint">Loading…</span>}
      {presets.status === 'error' && <span className="preset-hint error">Presets unavailable</span>}
      {items.map((preset) => {
        const empty = preset.members.length === 0;
        const flagged = (preset.unavailable && preset.unavailable.length > 0) || !preset.chairman;
        const lastUsed = preset.id === LAST_USED_PRESET_ID ? lastUsedInfo(preset) : null;
        const kind = lastUsed ? 'builtin last-used' : preset.builtin ? 'builtin' : 'user';
        return (
          <Fragment key={preset.id}>
            <span className={`preset-chip ${preset.id === activePresetId ? 'active' : ''} ${kind}`}>
              <button
                type="button"
                className="preset-apply"
                disabled={lastUsed ? lastUsed.disabled : empty}
                title={presetTitle(preset, lastUsed)}
                onClick={() => onApply(preset)}
              >
                {lastUsed && <span className="preset-glyph" aria-hidden="true">↺</span>}
                {preset.name}
                {flagged && !empty && <span className="preset-flag" aria-label="Some members are unavailable">!</span>}
              </button>
              {!preset.builtin && (
                <button
                  type="button"
                  className="preset-delete"
                  aria-label={`Delete preset ${preset.name}`}
                  title="Delete preset"
                  disabled={busy}
                  onClick={() => remove(preset)}
                >
                  ×
                </button>
              )}
            </span>
            {lastUsed && <span className="preset-divider" aria-hidden="true"></span>}
          </Fragment>
        );
      })}
      {naming ? (
        <form className="preset-save-form" onSubmit={submit}>
          <input
            type="text"
            className="preset-name-input"
            placeholder="Preset name"
            value={name}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.stopPropagation();
                setNaming(false);
              }
            }}
            autoFocus
          />
          <button type="submit" className="preset-save" disabled={!name.trim() || busy}>
            Save
          </button>
          <button type="button" className="preset-cancel" onClick={() => setNaming(false)}>
            Cancel
          </button>
        </form>
      ) : (
        <button
          type="button"
          className="preset-save-open"
          disabled={!canSave}
          title={canSave ? 'Save the current council as a preset' : 'Pick 1–8 members and a chairman first'}
          onClick={() => {
            setError(null);
            setNaming(true);
          }}
        >
          Save as preset…
        </button>
      )}
      {error && <span className="preset-hint error">{error}</span>}
    </div>
  );
}

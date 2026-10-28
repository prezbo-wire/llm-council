import { useState } from 'react';
import './PresetBar.css';

function presetTitle(preset) {
  const lines = [];
  if (preset.description) lines.push(preset.description);
  lines.push(`${preset.members.length} member${preset.members.length === 1 ? '' : 's'}`);
  if (!preset.chairman) lines.push('No chairman available: pick one after applying');
  if (preset.unavailable && preset.unavailable.length) lines.push(`Unavailable now: ${preset.unavailable.join(', ')}`);
  return lines.join('\n');
}

/**
 * One-click preset chips (built-in selectors and user presets), "Save as preset…" and delete
 * for user presets. Applying a preset with unavailable members still applies it; the picker
 * flags those members.
 */
export default function PresetBar({ presets, activePresetId, canSave, onApply, onSave, onDelete }) {
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

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

  return (
    <div className="preset-bar">
      <span className="preset-bar-label">Presets</span>
      {presets.status === 'loading' && <span className="preset-hint">Loading…</span>}
      {presets.status === 'error' && <span className="preset-hint error">Presets unavailable</span>}
      {items.map((preset) => {
        const empty = preset.members.length === 0;
        const flagged = (preset.unavailable && preset.unavailable.length > 0) || !preset.chairman;
        return (
          <span key={preset.id} className={`preset-chip ${preset.id === activePresetId ? 'active' : ''} ${preset.builtin ? 'builtin' : 'user'}`}>
            <button type="button" className="preset-apply" disabled={empty} title={presetTitle(preset)} onClick={() => onApply(preset)}>
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

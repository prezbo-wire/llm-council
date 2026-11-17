/**
 * Council draft logic for the picker (§I.5, §I.9): validation, initial draft resolution,
 * member/chair toggles, preset matching and the "Last used" preset.
 *
 * A draft is `{members: string[], chairman: string|null, presetId: string|null}`.
 */

export const MAX_MEMBERS = 8;
/** Mirrors backend/schemas.py MODEL_ID_PATTERN. */
export const MODEL_ID_RE = /^[a-z0-9][a-z0-9-]*\/\S{1,200}$/;
/** Mirrors backend/schemas.py PRESET_ID_PATTERN (user presets). */
export const PRESET_ID_RE = /^[a-z0-9-]{1,40}$/;
export const DEFAULT_PRESET_ID = 'builtin:hybrid';
/**
 * The backend's council of the most recent accepted send (backend/presets.py), listed first. It moves with
 * every send, so it is never a draft's presetId: applying it keeps the preset it came from (`source_preset_id`).
 */
export const LAST_USED_PRESET_ID = 'builtin:last-used';

/** @typedef {{members: string[], chairman: string|null, presetId: string|null}} CouncilDraft */

const isString = (value) => typeof value === 'string' && value.length > 0;

/**
 * Accepts a draft, a ConversationCouncil (`preset_id`) or a Preset and returns a clean draft,
 * or null when the shape is unusable.
 * @returns {CouncilDraft|null}
 */
export function normalizeDraft(raw) {
  if (!raw || typeof raw !== 'object' || !Array.isArray(raw.members)) return null;
  if (!raw.members.every(isString)) return null;
  const presetId = raw.presetId ?? raw.preset_id ?? null;
  return {
    members: [...raw.members],
    chairman: isString(raw.chairman) ? raw.chairman : null,
    presetId: isString(presetId) ? presetId : null,
  };
}

/**
 * The draft a preset applies. Last used is tagged with the preset it came from while it still matches it,
 * else with another exact match, else null; never with `builtin:last-used` itself.
 * @returns {CouncilDraft|null}
 */
export function draftFromPreset(preset, presets = []) {
  if (!preset || !Array.isArray(preset.members)) return null;
  const members = [...preset.members];
  const chairman = isString(preset.chairman) ? preset.chairman : null;
  if (preset.id === LAST_USED_PRESET_ID) {
    const source = isString(preset.source_preset_id) ? preset.source_preset_id : null;
    return withPresetMatch({ members, chairman, presetId: source }, presets);
  }
  return { members, chairman, presetId: preset.id ?? null };
}

/**
 * Fallback order (§I.9): the conversation's council, then the Last used preset (when it has members
 * and a chairman), then builtin:hybrid.
 * @returns {{draft: CouncilDraft|null, source: 'conversation'|'last-used'|'preset'|null}}
 */
export function resolveInitialDraft({ conversationCouncil = null, presets = [] } = {}) {
  const fromConversation = normalizeDraft(conversationCouncil);
  if (fromConversation && fromConversation.members.length > 0) return { draft: fromConversation, source: 'conversation' };
  const list = presets || [];
  const lastUsed = list.find((p) => p && p.id === LAST_USED_PRESET_ID);
  if (lastUsed && Array.isArray(lastUsed.members) && lastUsed.members.length > 0 && isString(lastUsed.chairman)) {
    return { draft: draftFromPreset(lastUsed, list), source: 'last-used' };
  }
  const hybrid = list.find((p) => p && p.id === DEFAULT_PRESET_ID);
  const fromPreset = draftFromPreset(hybrid);
  if (fromPreset) return { draft: fromPreset, source: 'preset' };
  return { draft: null, source: null };
}

export function sameCouncil(a, b) {
  if (!a || !b) return false;
  if ((a.chairman || null) !== (b.chairman || null)) return false;
  if (a.members.length !== b.members.length) return false;
  const set = new Set(a.members);
  return b.members.every((m) => set.has(m));
}

/**
 * Same members (any order), chairman and preset. Accepts drafts and ConversationCouncils (`preset_id`);
 * two missing councils are the same, a missing one never equals a present one.
 */
export function sameSelection(a, b) {
  const x = normalizeDraft(a);
  const y = normalizeDraft(b);
  if (!x || !y) return !x && !y;
  return sameCouncil(x, y) && x.presetId === y.presetId;
}

/**
 * Whether closing the picker should save the council: only when the user changed the selection while
 * the picker was open (`edited`) and it now differs from what it showed when opened. Opening and
 * closing it, or a default that changed underneath (presets reloaded), never writes.
 * The caller still skips the write when the draft equals the conversation's stored council.
 */
export function pickerShouldCommit({ edited = false, openedWith = null, draft = null, valid = false } = {}) {
  if (!edited || !valid || !draft) return false;
  return !sameSelection(openedWith, draft);
}

/**
 * Keeps `presetId` only while the draft still equals that preset; otherwise picks any exact match.
 * Last used is skipped: it equals every council just sent and would win over "My team" or "Hybrid".
 */
export function withPresetMatch(draft, presets) {
  if (!draft) return draft;
  const list = (Array.isArray(presets) ? presets : []).filter((p) => p && p.id !== LAST_USED_PRESET_ID);
  const current = list.find((p) => p.id === draft.presetId);
  if (current && sameCouncil(draft, current)) return draft;
  const match = list.find((p) => sameCouncil(draft, p));
  const presetId = match ? match.id : null;
  return presetId === draft.presetId ? draft : { ...draft, presetId };
}

/** Adds or removes a member (never beyond MAX_MEMBERS). */
export function toggleMember(draft, id) {
  const base = draft || { members: [], chairman: null, presetId: null };
  if (base.members.includes(id)) {
    return { ...base, members: base.members.filter((m) => m !== id), presetId: null };
  }
  if (base.members.length >= MAX_MEMBERS) return base;
  return { ...base, members: [...base.members, id], presetId: null };
}

/** Sets the chairman, or clears it when `id` already chairs. */
export function toggleChairman(draft, id) {
  const base = draft || { members: [], chairman: null, presetId: null };
  return { ...base, chairman: base.chairman === id ? null : id, presetId: null };
}

/** Request body for send / plan / PUT council. */
export function councilPayload(draft) {
  return { members: [...draft.members], chairman: draft.chairman };
}

/**
 * Checks the §I.5 rules (1–8 unique ids plus a chairman) and, when the catalog is loaded,
 * unknown ids and `can_chair`. Unavailable models are allowed (they fail fast at run time)
 * and are reported in `unavailable`.
 * @param {CouncilDraft|null} draft
 * @param {Map<string, object>|null} index catalog index, or null when not loaded
 */
export function validateDraft(draft, index = null) {
  const result = { valid: false, problems: [], unknown: [], unavailable: [] };
  if (!draft) {
    result.problems.push('Pick a council');
    return result;
  }
  const { members, chairman } = draft;
  if (members.length === 0) result.problems.push('Pick at least one member');
  if (members.length > MAX_MEMBERS) result.problems.push(`At most ${MAX_MEMBERS} members`);
  if (new Set(members).size !== members.length) result.problems.push('Each member can only be picked once');
  const badSyntax = [...members, ...(chairman ? [chairman] : [])].filter((id) => !MODEL_ID_RE.test(id));
  if (badSyntax.length) result.problems.push(`Invalid model id: ${[...new Set(badSyntax)].join(', ')}`);
  if (!chairman) result.problems.push('Pick a chairman');

  if (index) {
    const ids = [...new Set([...members, ...(chairman ? [chairman] : [])])];
    result.unknown = ids.filter((id) => !index.has(id));
    result.unavailable = ids.filter((id) => index.has(id) && index.get(id).available === false);
    if (result.unknown.length) result.problems.push(`Not in the Mese catalog: ${result.unknown.join(', ')}`);
    const chair = chairman ? index.get(chairman) : null;
    if (chair && chair.can_chair === false) result.problems.push(`${chair.name || chairman} cannot chair`);
  }
  result.valid = result.problems.length === 0;
  return result;
}

/** Turns a preset name into a user preset id (`^[a-z0-9-]{1,40}$`), avoiding `taken` ids. */
export function slugifyPresetName(name, taken = []) {
  const base =
    String(name || '')
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40)
      .replace(/-+$/, '') || 'preset';
  const used = new Set(taken);
  if (!used.has(base)) return base;
  for (let n = 2; n < 1000; n += 1) {
    const suffix = `-${n}`;
    const candidate = `${base.slice(0, 40 - suffix.length).replace(/-+$/, '')}${suffix}`;
    if (!used.has(candidate)) return candidate;
  }
  return base;
}

/**
 * Whether the Last used chip can be applied: `{disabled, reason}`; `reason` says why not (its tooltip).
 * Disabled before any send, and when it already is the current council.
 */
export function lastUsedChipState(preset, draft) {
  if (!preset || !Array.isArray(preset.members) || preset.members.length === 0) {
    return { disabled: true, reason: 'Nothing sent yet' };
  }
  if (draft && sameCouncil(draft, preset)) return { disabled: true, reason: 'Already the current council' };
  return { disabled: false, reason: null };
}

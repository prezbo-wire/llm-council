/**
 * Catalog helpers for CouncilModel (§I.5) records: lookup, display names, badges and filtering.
 * Pure functions, shared by the picker, member cards, Stage tabs and the final panel.
 */
import { MANAGE_USAGE, PLAN_NOTE, isPlanBilled } from './billing.js';

/**
 * Mese's residency class for a local model (Mese ARCHITECTURE §G.6). `effective` is 'exclusive' also for a
 * model too big to fit beside the resident set ("exclusive (auto)": `configured` differs).
 * @typedef {{effective: 'on_demand'|'resident'|'exclusive', configured: 'on_demand'|'resident'|'exclusive',
 *   est_load_s: number|null, observed_tok_s: number|null}} Residency
 */

/** @typedef {{id: string, name: string, kind: 'cloud'|'local'|'custom', provider: {id: string, name: string}|null,
 *   node: {id: string, name: string, runtime: string, host_id: string}|null, available: boolean,
 *   unavailable_reason: string|null, load_state: string|null, residency: Residency|null, can_rank: boolean,
 *   can_chair: boolean, context_length: number|null, size_bytes: number|null,
 *   price_per_mtok: {input: number, output: number}|null, price_verified: boolean|null,
 *   billing?: 'api_key'|'chatgpt_plan'|'local'|null, tags: string[],
 *   lineage: {name: string, version: string}|null}} CouncilModel */

/**
 * @typedef {{id: string, displayName: string, kind: 'cloud'|'local'|'custom'|null, providerId: string|null,
 *   nodeId: string|null, lineage: object|null, billing: string|null, legacy: boolean, inCatalog: boolean,
 *   model: CouncilModel|null}} ModelInfo
 */

/** @returns {Map<string, CouncilModel>} */
export function indexCatalog(models) {
  const index = new Map();
  for (const model of Array.isArray(models) ? models : []) {
    if (model && typeof model.id === 'string') index.set(model.id, model);
  }
  return index;
}

/** The part after the first slash ('openai/gpt-5.1' → 'gpt-5.1'); the whole id when there is none. */
export function shortModelName(id) {
  if (typeof id !== 'string') return '';
  const slash = id.indexOf('/');
  return slash === -1 ? id : id.slice(slash + 1) || id;
}

/**
 * Indexes a persisted `council_snapshot` (§I.7) by model id, as hints for describeModel().
 * @returns {Map<string, {displayName?: string, kind?: string, providerId?: string, nodeId?: string, lineage?: object,
 *   billing?: string}>}
 */
export function snapshotHints(snapshot) {
  const hints = new Map();
  if (!snapshot || typeof snapshot !== 'object') return hints;
  const add = (entry) => {
    if (!entry || typeof entry.id !== 'string') return;
    hints.set(entry.id, {
      displayName: entry.display_name,
      kind: entry.kind,
      providerId: entry.provider_id,
      nodeId: entry.node_id,
      lineage: entry.lineage,
      billing: entry.billing,
    });
  };
  (Array.isArray(snapshot.members) ? snapshot.members : []).forEach(add);
  add(snapshot.chairman);
  return hints;
}

/**
 * Resolves what the UI knows about a model id.
 * Order: the live catalog, then a hint (council_snapshot or council_started info), then the id itself.
 * An id is `legacy` (a pre-Mese model id) when the catalog is loaded, lacks it, and no hint describes it.
 * @param {string} id
 * @param {{index?: Map<string, CouncilModel>|null, hint?: object|null}} [context]
 * @returns {ModelInfo}
 */
export function describeModel(id, { index = null, hint = null } = {}) {
  const model = index ? index.get(id) || null : null;
  if (model) {
    return {
      id,
      displayName: model.name || shortModelName(id),
      kind: model.kind,
      providerId: model.provider ? model.provider.id : null,
      nodeId: model.node ? model.node.id : null,
      lineage: model.lineage || null,
      billing: model.billing || (hint && hint.billing) || null,
      legacy: false,
      inCatalog: true,
      model,
    };
  }
  const kind = hint && ['cloud', 'local', 'custom'].includes(hint.kind) ? hint.kind : null;
  return {
    id,
    displayName: (hint && hint.displayName) || shortModelName(id),
    kind,
    providerId: (hint && hint.providerId) || null,
    nodeId: (hint && hint.nodeId) || null,
    lineage: (hint && hint.lineage) || null,
    billing: (hint && hint.billing) || null,
    legacy: Boolean(index) && kind === null,
    inCatalog: false,
    model: null,
  };
}

/**
 * Badge for a model: `Cloud · anthropic` (blue), `Plan` (ChatGPT plan billing, teal), `Local · spark` (green),
 * `FT · v3` (purple), `Legacy` (grey). Returns null when nothing is known yet (catalog still loading and no hint).
 * @param {ModelInfo} info
 */
export function badgeFor(info) {
  if (!info) return null;
  if (info.legacy) {
    return { variant: 'legacy', label: 'Legacy', title: `${info.id} is not in the Mese catalog (older conversation)` };
  }
  if (isPlanBilled(info)) {
    return { variant: 'plan', label: 'Plan', title: 'Billed to your ChatGPT plan: no API cost, counts toward its usage limits' };
  }
  if (info.kind === 'cloud') {
    const where = info.providerId || (info.model && info.model.provider && info.model.provider.name) || '';
    return { variant: 'cloud', label: where ? `Cloud · ${where}` : 'Cloud', title: `Cloud model${where ? ` via ${where}` : ''}` };
  }
  if (info.kind === 'custom') {
    const version = info.lineage && info.lineage.version;
    const name = info.lineage && info.lineage.name;
    const node = info.nodeId ? ` on ${info.nodeId}` : '';
    return {
      variant: 'ft',
      label: version ? `FT · ${version}` : 'FT',
      title: `Fine-tune${name ? ` ${name}` : ''}${version ? ` ${version}` : ''}${node}`,
    };
  }
  if (info.kind === 'local') {
    return { variant: 'local', label: info.nodeId ? `Local · ${info.nodeId}` : 'Local', title: `Local model${info.nodeId ? ` on ${info.nodeId}` : ''}` };
  }
  return null;
}

/**
 * True when any of `ids` is billed to the ChatGPT plan, from the catalog or, for ids it lacks, the hints.
 * @param {Iterable<string>} ids
 * @param {{index?: Map<string, CouncilModel>|null, hints?: Map<string, object>|null}} [context]
 */
export function usesPlan(ids, { index = null, hints = null } = {}) {
  for (const id of ids || []) {
    if (typeof id === 'string' && isPlanBilled(describeModel(id, { index, hint: hints ? hints.get(id) || null : null }))) return true;
  }
  return false;
}

/**
 * "Using ChatGPT plan · Manage usage" for the picker while a member or the chairman is plan-billed, else null.
 * @returns {{text: string, action: {label: string, href: string}}|null}
 */
export function planUsageNote(draft, index = null) {
  if (!draft) return null;
  const ids = [...(Array.isArray(draft.members) ? draft.members : []), ...(draft.chairman ? [draft.chairman] : [])];
  return usesPlan(ids, { index }) ? { text: PLAN_NOTE, action: MANAGE_USAGE } : null;
}

/** Picker filter: 'all' | 'local' (local and custom/fine-tunes) | 'cloud'. */
export function matchesFilter(model, filter) {
  if (filter === 'cloud') return model.kind === 'cloud';
  if (filter === 'local') return model.kind === 'local' || model.kind === 'custom';
  return true;
}

/** Case-insensitive match on id, name, provider, node and tags. */
export function matchesSearch(model, query) {
  const q = typeof query === 'string' ? query.trim().toLowerCase() : '';
  if (!q) return true;
  const haystack = [
    model.id,
    model.name,
    model.provider && model.provider.name,
    model.provider && model.provider.id,
    model.node && model.node.name,
    model.node && model.node.id,
    model.lineage && model.lineage.name,
    ...(Array.isArray(model.tags) ? model.tags : []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return q.split(/\s+/).every((term) => haystack.includes(term));
}

const KIND_ORDER = { cloud: 0, local: 1, custom: 2 };

/** Stable order for the picker: available first, then cloud / local / fine-tune, then catalog order. */
export function sortForPicker(models) {
  return models
    .map((model, position) => ({ model, position }))
    .sort((a, b) => {
      if (a.model.available !== b.model.available) return a.model.available ? -1 : 1;
      const kind = (KIND_ORDER[a.model.kind] ?? 3) - (KIND_ORDER[b.model.kind] ?? 3);
      return kind !== 0 ? kind : a.position - b.position;
    })
    .map((entry) => entry.model);
}

/** Load-state dot tone for local models; null for cloud models (no dot). */
export function loadDotFor(model) {
  if (!model || model.kind === 'cloud') return null;
  switch (model.load_state) {
    case 'loaded':
      return { tone: 'loaded', label: 'Loaded' };
    case 'loading':
      return { tone: 'loading', label: 'Loading' };
    case 'downloading':
      return { tone: 'loading', label: 'Downloading' };
    case 'unloaded':
      return { tone: 'unloaded', label: 'Not loaded (loads on first use)' };
    case 'sleeping':
      return { tone: 'unloaded', label: 'Sleeping (wakes on first use)' };
    default:
      return { tone: 'unknown', label: 'Load state unknown' };
  }
}

/* ── Residency (Mese ARCHITECTURE §G.6, §I.9) ── */

/** Mese's `slow` plan warning threshold: a measured decode rate below this many tokens per second. */
export const SLOW_TOK_PER_S = 10;

const isSeconds = (value) => typeof value === 'number' && Number.isFinite(value) && value >= 0;

/** "~48 s" below 90 s, else "~3 min". */
export function approxDuration(seconds) {
  if (seconds < 90) return `~${Math.max(1, Math.round(seconds))} s`;
  return `~${Math.round(seconds / 60)} min`;
}

/** "4.4" below 10 tok/s, else a whole number. */
function tokRate(tokPerSec) {
  return tokPerSec < 10 ? String(Number(tokPerSec.toFixed(1))) : String(Math.round(tokPerSec));
}

/**
 * Residency pills for a picker row: the class pill, then "Slow" when Mese measured under 10 tok/s, then
 * "CPU offload" when the plan warned about it. Models without residency data (cloud, a runtime Mese does
 * not load, an older Mese) get no class pill.
 *
 * | state                  | label                        | tone           |
 * |------------------------|------------------------------|----------------|
 * | resident, loaded       | Resident                     | resident       |
 * | resident, not loaded   | Resident · reloading         | reloading      |
 * | on_demand              | On demand · ~N s / · loaded  | on-demand      |
 * | exclusive (configured) | Exclusive · ~N s / · loaded  | exclusive      |
 * | exclusive (auto)       | Exclusive (auto) · ~N s      | exclusive-auto |
 * | observed_tok_s < 10    | Slow · ~N tok/s              | slow           |
 * | cpu_offload warning    | CPU offload                  | cpu-offload    |
 *
 * @param {CouncilModel} model
 * @param {{cpuOffload?: string|null}} [plan] the message of the plan's `cpu_offload` warning for this model
 * @returns {{tone: string, label: string, title: string}[]}
 */
export function residencyHintFor(model, { cpuOffload = null } = {}) {
  const pills = [];
  const residency = model && model.kind !== 'cloud' ? model.residency : null;
  if (residency) {
    const host = (model.node && (model.node.host_id || model.node.id)) || 'its host';
    const loadS = isSeconds(residency.est_load_s) ? residency.est_load_s : null;
    const tok = isSeconds(residency.observed_tok_s) && residency.observed_tok_s > 0 ? residency.observed_tok_s : null;
    const loaded = model.load_state === 'loaded' || loadS === 0;
    const wait = loaded ? ' · loaded' : loadS === null ? '' : ` · ${approxDuration(loadS)}`;
    if (residency.effective === 'resident' && loaded) {
      const recent = tok === null ? '' : ` (~${tokRate(tok)} tok/s recently)`;
      pills.push({ tone: 'resident', label: 'Resident', title: `Always loaded on ${host}${recent}` });
    } else if (residency.effective === 'resident') {
      const when = loadS === null ? 'soon' : `in ${approxDuration(loadS)}`;
      pills.push({ tone: 'reloading', label: 'Resident · reloading', title: `Reloads ${when} (after a large model or a restart)` });
    } else if (residency.effective === 'exclusive' && residency.configured !== 'exclusive') {
      pills.push({
        tone: 'exclusive-auto',
        label: `Exclusive (auto)${wait}`,
        title: 'Too big to fit beside the resident models; runs like an exclusive model',
      });
    } else if (residency.effective === 'exclusive') {
      pills.push({ tone: 'exclusive', label: `Exclusive${wait}`, title: 'Unloads resident models while it runs; they reload afterwards' });
    } else {
      pills.push({ tone: 'on-demand', label: `On demand${wait}`, title: 'Loads into spare memory on first use' });
    }
    if (tok !== null && tok < SLOW_TOK_PER_S) {
      const minutes = Math.max(1, Math.round(1000 / tok / 60));
      pills.push({ tone: 'slow', label: `Slow · ~${tokRate(tok)} tok/s`, title: `A 1,000-token answer takes ~${minutes} min` });
    }
  }
  if (cpuOffload) pills.push({ tone: 'cpu-offload', label: 'CPU offload', title: cpuOffload });
  return pills;
}

/** The plan's `cpu_offload` warning messages by model id (shown as a "CPU offload" pill on the row). */
export function cpuOffloadByModel(plan) {
  const byModel = new Map();
  for (const warning of plan && Array.isArray(plan.warnings) ? plan.warnings : []) {
    if (warning && warning.code === 'cpu_offload' && typeof warning.model === 'string' && typeof warning.message === 'string') {
      byModel.set(warning.model, warning.message);
    }
  }
  return byModel;
}

/**
 * The picker's turn line from `plan.turn` (Mese simulates the turn when the plan names the chairman):
 * "Local: no model loads" or "Local: 1 model load (+~48 s); residents reload ~52 s later".
 * Null without a turn (an older Mese) and for a council with no local model that loads nothing.
 * @returns {{tone: 'none'|'loads', text: string}|null}
 */
export function planTurnLine(plan) {
  const turn = plan && typeof plan === 'object' ? plan.turn : null;
  if (!turn || typeof turn !== 'object' || !Number.isInteger(turn.loads) || turn.loads < 0) return null;
  if (turn.loads === 0) {
    const items = Array.isArray(plan.items) ? plan.items : [];
    const local = items.some((item) => item && (item.kind === 'local' || item.kind === 'custom'));
    return local ? { tone: 'none', text: 'Local: no model loads' } : null;
  }
  const loads = `${turn.loads} model load${turn.loads === 1 ? '' : 's'}`;
  const loadS = isSeconds(turn.load_s) && turn.load_s > 0 ? ` (+${approxDuration(turn.load_s)})` : '';
  const restore = isSeconds(turn.restore_s) && turn.restore_s > 0 ? `; residents reload ${approxDuration(turn.restore_s)} later` : '';
  return { tone: 'loads', text: `Local: ${loads}${loadS}${restore}` };
}

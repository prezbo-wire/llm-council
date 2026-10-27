/**
 * Catalog helpers for CouncilModel (§I.5) records: lookup, display names, badges and filtering.
 * Pure functions, shared by the picker, member cards, Stage tabs and the final panel.
 */

/** @typedef {{id: string, name: string, kind: 'cloud'|'local'|'custom', provider: {id: string, name: string}|null,
 *   node: {id: string, name: string, runtime: string, host_id: string}|null, available: boolean,
 *   unavailable_reason: string|null, load_state: string|null, can_rank: boolean, can_chair: boolean,
 *   context_length: number|null, size_bytes: number|null, price_per_mtok: {input: number, output: number}|null,
 *   price_verified: boolean|null, tags: string[], lineage: {name: string, version: string}|null}} CouncilModel */

/**
 * @typedef {{id: string, displayName: string, kind: 'cloud'|'local'|'custom'|null, providerId: string|null,
 *   nodeId: string|null, lineage: object|null, legacy: boolean, inCatalog: boolean, model: CouncilModel|null}} ModelInfo
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
 * @returns {Map<string, {displayName?: string, kind?: string, providerId?: string, nodeId?: string, lineage?: object}>}
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
    legacy: Boolean(index) && kind === null,
    inCatalog: false,
    model: null,
  };
}

/**
 * Badge for a model: `Cloud · anthropic` (blue), `Local · spark` (green), `FT · v3` (purple), `Legacy` (grey).
 * Returns null when nothing is known yet (catalog still loading and no hint).
 * @param {ModelInfo} info
 */
export function badgeFor(info) {
  if (!info) return null;
  if (info.legacy) {
    return { variant: 'legacy', label: 'Legacy', title: `${info.id} is not in the Mese catalog (older conversation)` };
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

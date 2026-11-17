/**
 * Mese status pill and council plan helpers (pure).
 */

/**
 * Pill state from the `/api/mese/health` resource.
 * @param {{status: 'loading'|'ready'|'error', data: object|null, error: Error|null}} health
 * @returns {{tone: 'ok'|'warn'|'error'|'unknown', label: string, detail: string|null}}
 */
export function meseStatus(health) {
  if (!health || health.status === 'loading') return { tone: 'unknown', label: 'checking', detail: null };
  if (health.status === 'error' || !health.data) {
    const message = health.error && health.error.message ? health.error.message : null;
    return { tone: 'error', label: 'unreachable', detail: message || 'LLM Council backend is unreachable' };
  }
  const data = health.data;
  if (!data.ok) {
    if (data.token_set === false) {
      return { tone: 'error', label: 'unreachable', detail: data.error || 'MESE_API_KEY is not set in .env' };
    }
    return { tone: 'error', label: 'unreachable', detail: data.error || 'Mese did not answer' };
  }
  if (data.locked) return { tone: 'warn', label: 'locked', detail: 'Keyring unavailable: cloud keys cannot be used' };
  return { tone: 'ok', label: 'connected', detail: null };
}

/** Plan warning codes the picker already shows per model (unavailable chips and rows). */
const PER_MODEL_CODES = new Set(['unavailable', 'no_credential']);

/**
 * Warning lines for the picker from a V1PlanResponse (deduplicated, in order). Availability
 * warnings for models in `alreadyFlagged` are dropped because the picker flags those models itself.
 * @returns {{code: string, message: string}[]}
 */
export function planWarnings(plan, alreadyFlagged = []) {
  if (!plan || typeof plan !== 'object') return [];
  const flagged = new Set(alreadyFlagged);
  const out = [];
  const seen = new Set();
  const add = (code, message) => {
    if (!message || seen.has(message)) return;
    seen.add(message);
    out.push({ code, message });
  };
  if (plan.feasible === false) add('infeasible', 'Mese cannot fit this council on the available hardware');
  for (const warning of Array.isArray(plan.warnings) ? plan.warnings : []) {
    if (!warning || typeof warning.message !== 'string') continue;
    if (PER_MODEL_CODES.has(warning.code) && warning.model && flagged.has(warning.model)) continue;
    add(warning.code || 'warning', warning.message);
  }
  return out;
}

/**
 * Stable key for a draft (member order does not matter for planning); '' for no draft.
 * It is JSON so the effect that fetches the plan can rebuild the request from the key alone.
 */
export function planKey(draft) {
  if (!draft) return '';
  return JSON.stringify({ members: [...draft.members].sort(), chairman: draft.chairman || null });
}

/** At most one catalog/preset reload per this many ms on window focus (the backend caches /v1/models for 10 s). */
export const FOCUS_RESOURCE_REFRESH_MS = 10_000;

/**
 * The `/api/mese/health` fields whose change means the catalog or presets may have changed (a key added
 * in Mese admin, a node up or down, a breaker opened, the keyring locked or unlocked); null when Mese
 * did not answer.
 */
export function healthSignature(data) {
  if (!data || !data.ok) return null;
  const pick = (key) => (data[key] === undefined ? null : data[key]);
  return JSON.stringify(['providers_configured', 'nodes_up', 'nodes_total', 'breakers_open', 'locked', 'version'].map(pick));
}

/**
 * Whether a health tick should reload the catalog and presets:
 * `{force: true}` (skip the backend's catalog cache) when Mese came back or its health signature changed,
 * `{force: false}` on a window focus at least FOCUS_RESOURCE_REFRESH_MS after the last reload, else null.
 */
export function resourceRefresh({ prevOk = null, prevSignature = null, ok, signature, focus = false, sinceLastMs = 0 }) {
  if (!ok) return null;
  if (prevOk === false) return { force: true };
  if (prevSignature !== null && signature !== prevSignature) return { force: true };
  if (focus && sinceLastMs >= FOCUS_RESOURCE_REFRESH_MS) return { force: false };
  return null;
}

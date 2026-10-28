/**
 * Display formatting for council UI: prices, costs, tokens, durations, error and status texts.
 * Pure functions (no React) so they are unit-tested with `node --test`.
 */

function trimZeros(text) {
  return text.includes('.') ? text.replace(/0+$/, '').replace(/\.$/, '') : text;
}

/** USD per 1M tokens: 4 → "$4", 0.1 → "$0.1", 1.25 → "$1.25", 0.0375 → "$0.0375". */
export function formatUsdPerMtok(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '?';
  if (value === 0) return '$0';
  const digits = value >= 1 ? 2 : 4;
  return `$${trimZeros(value.toFixed(digits))}`;
}

/**
 * Picker price label: "$4 / $20", "free · local", "price unverified".
 * @returns {{text: string, tone: 'price'|'free'|'unverified', title: string}}
 */
export function formatModelPrice(model) {
  if (!model) return { text: '', tone: 'price', title: '' };
  if (model.kind === 'local' || model.kind === 'custom') {
    return { text: 'free · local', tone: 'free', title: 'Runs on your own hardware' };
  }
  const price = model.price_per_mtok;
  if (!price || typeof price.input !== 'number' || typeof price.output !== 'number') {
    return { text: 'price unverified', tone: 'unverified', title: 'Mese has no price for this model' };
  }
  const text = `${formatUsdPerMtok(price.input)} / ${formatUsdPerMtok(price.output)}`;
  if (model.price_verified === false) {
    return { text: `${text} · unverified`, tone: 'unverified', title: 'USD per 1M input / output tokens (seed price, not verified)' };
  }
  return { text, tone: 'price', title: 'USD per 1M input / output tokens' };
}

/** Run or call cost: 0 → "$0", 0.0123 → "$0.0123", 0.00412 → "$0.00412", 1.5 → "$1.50". */
export function formatCost(usd) {
  if (typeof usd !== 'number' || !Number.isFinite(usd)) return null;
  if (usd === 0) return '$0';
  if (usd < 0.0001) return '<$0.0001';
  if (usd < 1) return `$${Number(usd.toPrecision(3)).toString()}`;
  return `$${usd.toFixed(2)}`;
}

/** Token counts: 950 → "950", 1400 → "1.4k", 12_345 → "12k", 1_200_000 → "1.2M". */
export function formatTokens(count) {
  if (typeof count !== 'number' || !Number.isFinite(count)) return null;
  if (count < 1000) return String(Math.round(count));
  if (count < 10_000) return `${trimZeros((count / 1000).toFixed(1))}k`;
  if (count < 1_000_000) return `${Math.round(count / 1000)}k`;
  return `${trimZeros((count / 1_000_000).toFixed(1))}M`;
}

/** Seconds for latencies: 6200 → "6.2s". */
export function formatLatency(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms)) return null;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Elapsed timer text: 12_400 → "12 s", 75_000 → "1 m 15 s". */
export function formatElapsed(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return '0 s';
  const total = Math.floor(ms / 1000);
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return seconds ? `${minutes} m ${seconds} s` : `${minutes} m`;
}

/** Card footer: "$0.0123 · 1.4k tok". Total tokens = prompt + completion. */
export function formatUsageFooter({ costUsd = null, usage = null } = {}) {
  const parts = [];
  const cost = formatCost(costUsd);
  if (cost) parts.push(cost);
  if (usage && typeof usage === 'object') {
    const total = (usage.promptTokens ?? usage.prompt_tokens ?? 0) + (usage.completionTokens ?? usage.completion_tokens ?? 0);
    const tokens = formatTokens(total);
    if (total > 0 && tokens) parts.push(`${tokens} tok`);
  }
  return parts.join(' · ');
}

/** Human text for Mese / llm-council error codes (§D.3, §I.2, §I.5). */
export const ERROR_TEXT = Object.freeze({
  credential_missing: 'No API key in Mese',
  content_filtered: 'Declined by provider',
  mese_unreachable: 'Mese is unreachable',
  mese_locked: 'Mese is locked (keyring unavailable)',
  backend_unreachable: 'LLM Council backend is unreachable',
  circuit_open: 'Temporarily unavailable (circuit open)',
  upstream_rate_limited: 'Rate limited by the provider',
  upstream_timeout: 'Timed out waiting for the model',
  upstream_auth_error: 'Provider rejected the API key',
  upstream_bad_request: 'Provider rejected the request',
  upstream_error: 'Provider error',
  load_failed: 'Model failed to load',
  load_timeout: 'Model took too long to load',
  queue_full: 'Node queue is full',
  queue_timeout: 'Waited too long in the queue',
  node_unavailable: 'Node unavailable',
  budget_exceeded: 'Daily cloud budget reached',
  model_not_found: 'Model not found in Mese',
  context_length_exceeded: 'Prompt is too long for this model',
  unauthorized: 'Mese rejected the llm-council token',
  invalid_request: 'Invalid request',
  cancelled: 'Cancelled',
  internal: 'Internal Mese error',
  empty_response: 'No answer text',
  run_failed: 'The council run failed',
  no_responses: 'No council member responded',
  unknown_models: 'Some models are not in the Mese catalog',
  chairman_cannot_chair: 'This chairman cannot chair',
  council_required: 'Pick a council first',
});

/**
 * @returns {{title: string, detail: string|null, code: string|null}} title is the human text,
 *   detail the raw message when it adds information.
 */
export function humanizeError(code, message) {
  const raw = typeof message === 'string' ? message.trim() : '';
  const known = code && Object.prototype.hasOwnProperty.call(ERROR_TEXT, code) ? ERROR_TEXT[code] : null;
  if (known) {
    const detail = raw && raw.toLowerCase() !== known.toLowerCase() ? raw : null;
    return { title: known, detail, code: code || null };
  }
  return { title: raw || 'Failed to respond', detail: null, code: code || null };
}

/** Message for an ApiError raised by a send / save (unknown_models lists the bad ids). */
export function describeApiError(error) {
  if (!error) return '';
  const code = error.code || null;
  if (code === 'unknown_models' && error.detail && Array.isArray(error.detail.bad_ids)) {
    return `Not in the Mese catalog: ${error.detail.bad_ids.join(', ')}`;
  }
  const { title, detail } = humanizeError(code, error.message);
  return detail ? `${title}: ${detail}` : title;
}

/** Unavailable reasons from /v1/models (§D.1); `admin` marks reasons fixed in the Mese admin panel. */
const UNAVAILABLE_TEXT = {
  no_credential: { text: 'No API key: add it in Mese admin', admin: true },
  locked: { text: 'Locked', admin: true },
  provider_disabled: { text: 'Provider disabled in Mese', admin: true },
  node_down: { text: 'Node down', admin: true },
  node_disabled: { text: 'Node disabled in Mese', admin: true },
  not_installed: { text: 'Not installed on the node', admin: true },
  circuit_open: { text: 'Temporarily unavailable (circuit open)', admin: false },
};

export function unavailableText(reason) {
  if (reason && UNAVAILABLE_TEXT[reason]) return UNAVAILABLE_TEXT[reason];
  return { text: reason ? `Unavailable (${reason})` : 'Unavailable', admin: false };
}

/**
 * Chairman cost of a stored turn: `stage3.cost_usd`, else (turns stored before that field existed) the
 * Stage-3 total from `usage.by_stage`, used only when positive because 0 there also means "unknown".
 */
export function chairmanCostUsd(stage3, usage) {
  if (stage3 && typeof stage3.cost_usd === 'number' && Number.isFinite(stage3.cost_usd)) return stage3.cost_usd;
  const byStage = usage && usage.by_stage && typeof usage.by_stage === 'object' ? usage.by_stage['3'] : null;
  return typeof byStage === 'number' && Number.isFinite(byStage) && byStage > 0 ? byStage : null;
}

/** Badge for a stored turn's `status` (§I.7): partial, or a run that stopped early; null for complete. */
export function turnStatusBadge(status) {
  if (status === 'partial') return { text: 'partial', title: 'Some calls failed in this turn' };
  if (status === 'cancelled') return { text: 'cancelled', title: 'The run was cancelled before it finished' };
  if (status === 'error') return { text: 'failed', title: 'The run stopped on an unexpected error' };
  return null;
}

/** True for a stored turn that stopped before the council finished. */
export function isUnfinishedTurn(status) {
  return status === 'cancelled' || status === 'error';
}

export function stage2SkipText(reason) {
  if (reason === 'fewer_than_two_responses') return 'Peer ranking skipped: fewer than two responses';
  if (reason === 'no_rankers') return 'Peer ranking skipped: no member can rank';
  return 'Peer ranking skipped';
}

/** "Retrying (rate limited) · attempt 2". */
export function retryText(attempt, reason) {
  const why = reason ? ` (${(ERROR_TEXT[reason] || reason).toLowerCase()})` : '';
  return `Retrying${why}${typeof attempt === 'number' ? ` · attempt ${attempt}` : ''}`;
}

/** "Queued #2 on spark" / "Queued". */
export function queuedText(position, nodeId) {
  return `Queued${typeof position === 'number' ? ` #${position}` : ''}${nodeId ? ` on ${nodeId}` : ''}`;
}

/** "Loading on spark…" (the elapsed timer is appended by the component). */
export function loadingText(nodeId) {
  return `Loading${nodeId ? ` on ${nodeId}` : ''}…`;
}

/**
 * Pure reducer for the council run stream (`POST …/message/stream/v2`, Mese ARCHITECTURE §I.6).
 *
 * Plain JS (no JSX, no React) so it runs under `node --test`. Every handler returns a new state
 * object and never mutates its input; unknown event types return the input state unchanged.
 * `now` (ms) is injected by the caller so the reducer stays pure; it only stamps `since` fields
 * that drive "Loading on spark… 12 s" style timers.
 */

/** Every event type the backend may emit (mirrors backend/contracts.py SSE_EVENTS). */
export const COUNCIL_EVENT_TYPES = Object.freeze([
  'council_started', 'member_queued', 'member_status', 'member_streaming', 'member_complete', 'member_error',
  'stage1_complete', 'stage2_start', 'stage2_member_status', 'stage2_model_complete', 'stage2_member_error',
  'stage2_member_skipped', 'stage2_skipped', 'stage2_complete', 'final_generating', 'final_status',
  'final_streaming', 'final_ready', 'final_error', 'stage3_complete', 'title_complete', 'run_summary',
  'complete', 'error',
]);

/** Mese member states (`member_status.state`, §I.3 MemberState). */
const MESE_STATES = new Set(['queued', 'loading', 'started', 'retrying', 'thinking']);
const MEMBER_TERMINAL = new Set(['done', 'error']);
const RANKER_TERMINAL = new Set(['done', 'failed', 'skipped']);
const FINAL_TERMINAL = new Set(['ready', 'error']);

/** Mese state → Stage-2 ranker row status. */
const RANKER_STATE = { queued: 'queued', loading: 'loading', started: 'ranking', thinking: 'ranking', retrying: 'retrying' };
/** Mese state → chairman panel status. */
const FINAL_STATE = { queued: 'queued', loading: 'loading', started: 'generating', thinking: 'thinking', retrying: 'retrying' };

export function createRunState() {
  return {
    phase: 'idle', // idle | stage1 | stage2 | stage3 | complete | error
    runId: null,
    members: {},
    memberOrder: [],
    chairman: null, // {modelId, displayName, kind, providerId, nodeId}
    stage1: null,
    stage2: {
      status: 'idle', // idle | running | skipped | complete
      skippedReason: null,
      rankers: {},
      rankerOrder: [],
      rankings: null,
      labelToModel: null,
      aggregateRankings: null,
      failures: [],
    },
    final: {
      status: 'empty', // empty | generating | queued | loading | retrying | thinking | streaming | ready | error
      content: '',
      latencyMs: null,
      costUsd: null,
      model: null,
      error: null, // {code, message}
      position: null,
      nodeId: null,
      attempt: null,
      reason: null,
      since: null,
    },
    stage3: null,
    title: null,
    summary: null, // run_summary payload
    error: null, // unexpected exception message
  };
}

function pickDefined(source, keys) {
  const out = {};
  for (const key of keys) {
    if (source[key] !== undefined) out[key] = source[key];
  }
  return out;
}

const MEMBER_INFO_KEYS = ['displayName', 'kind', 'providerId', 'nodeId', 'canRank'];

function newMember(memberId, info = {}) {
  return {
    memberId,
    displayName: memberId,
    kind: null,
    providerId: null,
    nodeId: null,
    canRank: null,
    status: 'queued',
    position: null,
    attempt: null,
    reason: null,
    since: null,
    progressText: '',
    latencyMs: null,
    ttftMs: null,
    costUsd: null,
    usage: null,
    error: null,
    ...pickDefined(info, MEMBER_INFO_KEYS),
  };
}

/** Returns state with `memberId` replaced by `update(member)`; creates the member when missing. */
function withMember(state, memberId, update) {
  if (typeof memberId !== 'string' || !memberId) return state;
  const existing = state.members[memberId];
  const base = existing || newMember(memberId);
  const next = update(base);
  if (next === base && existing) return state;
  return {
    ...state,
    phase: state.phase === 'idle' ? 'stage1' : state.phase,
    members: { ...state.members, [memberId]: next },
    memberOrder: existing ? state.memberOrder : [...state.memberOrder, memberId],
  };
}

function newRanker(memberId) {
  return { memberId, status: 'pending', ranking: null, error: null, reason: null, position: null, attempt: null, since: null };
}

function withRanker(state, memberId, update) {
  if (typeof memberId !== 'string' || !memberId) return state;
  const existing = state.stage2.rankers[memberId];
  const base = existing || newRanker(memberId);
  const next = update(base);
  if (next === base && existing) return state;
  return {
    ...state,
    phase: phaseAtLeast(state.phase, 'stage2'),
    stage2: {
      ...state.stage2,
      status: state.stage2.status === 'idle' ? 'running' : state.stage2.status,
      rankers: { ...state.stage2.rankers, [memberId]: next },
      rankerOrder: existing ? state.stage2.rankerOrder : [...state.stage2.rankerOrder, memberId],
    },
  };
}

const PHASES = ['idle', 'stage1', 'stage2', 'stage3', 'complete'];

function phaseAtLeast(current, wanted) {
  if (current === 'error' || current === 'complete') return current;
  return PHASES.indexOf(current) >= PHASES.indexOf(wanted) ? current : wanted;
}

function withFinal(state, patch) {
  return { ...state, phase: phaseAtLeast(state.phase, 'stage3'), final: { ...state.final, ...patch } };
}

function errorOf(event) {
  return {
    code: typeof event.code === 'string' ? event.code : null,
    message: typeof event.message === 'string' ? event.message : '',
  };
}

function numberOrNull(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/* ── Stage 1 ─────────────────────────────────────────────────────────────── */

function onCouncilStarted(state, event) {
  const fresh = createRunState();
  const members = {};
  const memberOrder = [];
  for (const info of Array.isArray(event.members) ? event.members : []) {
    if (!info || typeof info.memberId !== 'string' || members[info.memberId]) continue;
    members[info.memberId] = newMember(info.memberId, info);
    memberOrder.push(info.memberId);
  }
  return {
    ...fresh,
    phase: 'stage1',
    runId: event.runId ?? null,
    chairman: event.chairman && typeof event.chairman.modelId === 'string' ? { ...event.chairman } : null,
    members,
    memberOrder,
  };
}

function onMemberQueued(state, event) {
  return withMember(state, event.memberId, (m) => ({
    ...m,
    ...pickDefined(event, MEMBER_INFO_KEYS),
    status: m.status === 'queued' ? 'queued' : m.status,
  }));
}

function onMemberStatus(state, event, now) {
  if (!MESE_STATES.has(event.state)) return state;
  return withMember(state, event.memberId, (m) => {
    if (MEMBER_TERMINAL.has(m.status)) return m;
    // Reasoning can arrive after content on some runtimes; never downgrade a streaming card.
    if (event.state === 'thinking' && m.status === 'streaming') return m;
    const changed = m.status !== event.state;
    return {
      ...m,
      status: event.state,
      position: event.state === 'queued' ? numberOrNull(event.position) : null,
      nodeId: event.nodeId ?? m.nodeId,
      attempt: numberOrNull(event.attempt) ?? m.attempt,
      reason: event.reason ?? null,
      since: changed || m.since === null ? now : m.since,
    };
  });
}

function onMemberStreaming(state, event, now) {
  const delta = typeof event.deltaText === 'string' ? event.deltaText : '';
  return withMember(state, event.memberId, (m) => {
    if (MEMBER_TERMINAL.has(m.status)) return m;
    return {
      ...m,
      status: 'streaming',
      position: null,
      since: m.status === 'streaming' ? m.since : now,
      progressText: m.progressText + delta,
    };
  });
}

function onMemberComplete(state, event) {
  return withMember(state, event.memberId, (m) => ({
    ...m,
    status: 'done',
    position: null,
    progressText: typeof event.response === 'string' ? event.response : m.progressText,
    latencyMs: numberOrNull(event.latencyMs),
    ttftMs: numberOrNull(event.ttftMs),
    costUsd: numberOrNull(event.costUsd),
    usage: event.usage && typeof event.usage === 'object' ? { ...event.usage } : null,
    error: null,
  }));
}

function onMemberError(state, event) {
  return withMember(state, event.memberId, (m) => {
    // member_error is Stage 1 only; it must never overwrite a card that already finished.
    if (m.status === 'done') return m;
    return {
      ...m,
      status: 'error',
      position: null,
      error: { ...errorOf(event), retryable: event.retryable === true },
    };
  });
}

/* ── Stage 2 ─────────────────────────────────────────────────────────────── */

function onStage2Start(state) {
  const rankers = { ...state.stage2.rankers };
  const rankerOrder = [...state.stage2.rankerOrder];
  // Rankers are Stage-1 successes with can_rank (§I.4); show them as pending rows right away.
  for (const id of state.memberOrder) {
    const m = state.members[id];
    if (m.status === 'done' && m.canRank === true && !rankers[id]) {
      rankers[id] = newRanker(id);
      rankerOrder.push(id);
    }
  }
  return {
    ...state,
    phase: phaseAtLeast(state.phase, 'stage2'),
    stage2: { ...state.stage2, status: 'running', rankers, rankerOrder },
  };
}

function onStage2MemberStatus(state, event, now) {
  const status = RANKER_STATE[event.state];
  if (!status) return state;
  return withRanker(state, event.memberId, (r) => {
    if (RANKER_TERMINAL.has(r.status)) return r;
    return {
      ...r,
      status,
      position: status === 'queued' ? numberOrNull(event.position) : null,
      attempt: numberOrNull(event.attempt) ?? r.attempt,
      reason: event.reason ?? null,
      since: r.status === status && r.since !== null ? r.since : now,
    };
  });
}

function onStage2ModelComplete(state, event) {
  return withRanker(state, event.memberId, (r) => ({
    ...r,
    status: 'done',
    position: null,
    ranking: Array.isArray(event.ranking) ? [...event.ranking] : event.ranking ?? null,
    error: null,
  }));
}

function onStage2MemberError(state, event) {
  return withRanker(state, event.memberId, (r) => (r.status === 'done' ? r : { ...r, status: 'failed', position: null, error: errorOf(event) }));
}

function onStage2MemberSkipped(state, event) {
  return withRanker(state, event.memberId, (r) => ({
    ...r,
    status: 'skipped',
    position: null,
    reason: typeof event.reason === 'string' ? event.reason : 'context',
  }));
}

function onStage2Skipped(state, event) {
  return {
    ...state,
    phase: phaseAtLeast(state.phase, 'stage2'),
    stage2: {
      ...state.stage2,
      status: 'skipped',
      skippedReason: typeof event.reason === 'string' ? event.reason : null,
      rankers: {},
      rankerOrder: [],
    },
  };
}

function onStage2Complete(state, event) {
  const rankings = Array.isArray(event.data) ? event.data : [];
  const metadata = event.metadata && typeof event.metadata === 'object' ? event.metadata : {};
  const ranked = new Set(rankings.map((entry) => entry && entry.model));
  const rankers = {};
  for (const id of state.stage2.rankerOrder) {
    const r = state.stage2.rankers[id];
    rankers[id] = !RANKER_TERMINAL.has(r.status) && ranked.has(id) ? { ...r, status: 'done', position: null } : r;
  }
  return {
    ...state,
    phase: phaseAtLeast(state.phase, 'stage2'),
    stage2: {
      ...state.stage2,
      status: state.stage2.status === 'skipped' ? 'skipped' : 'complete',
      rankers,
      rankings,
      labelToModel: metadata.label_to_model ?? null,
      aggregateRankings: metadata.aggregate_rankings ?? null,
      failures: Array.isArray(metadata.failures) ? metadata.failures : [],
    },
  };
}

/* ── Stage 3 and run end ─────────────────────────────────────────────────── */

function onFinalGenerating(state, event, now) {
  if (FINAL_TERMINAL.has(state.final.status)) return state;
  return withFinal(state, {
    status: 'generating',
    content: '',
    since: now,
    model: state.final.model ?? state.chairman?.modelId ?? null,
  });
}

function onFinalStatus(state, event, now) {
  const status = FINAL_STATE[event.state];
  const current = state.final.status;
  if (!status || FINAL_TERMINAL.has(current)) return state;
  if (status === 'thinking' && current === 'streaming') return state;
  return withFinal(state, {
    status,
    position: status === 'queued' ? numberOrNull(event.position) : null,
    nodeId: event.nodeId ?? state.final.nodeId,
    attempt: numberOrNull(event.attempt) ?? state.final.attempt,
    reason: event.reason ?? null,
    since: status === current && state.final.since !== null ? state.final.since : now,
  });
}

function onFinalStreaming(state, event, now) {
  if (FINAL_TERMINAL.has(state.final.status)) return state;
  const delta = typeof event.deltaText === 'string' ? event.deltaText : '';
  return withFinal(state, {
    status: 'streaming',
    position: null,
    since: state.final.status === 'streaming' ? state.final.since : now,
    content: state.final.content + delta,
  });
}

function onFinalReady(state, event) {
  return withFinal(state, {
    status: 'ready',
    position: null,
    content: typeof event.content === 'string' ? event.content : state.final.content,
    latencyMs: numberOrNull(event.latencyMs),
    costUsd: numberOrNull(event.costUsd),
    model: event.model ?? state.final.model ?? state.chairman?.modelId ?? null,
    error: null,
  });
}

function onFinalError(state, event) {
  // Keep any partial text for context, but never present it as a finished answer.
  return withFinal(state, { status: 'error', position: null, error: errorOf(event) });
}

function onStage3Complete(state, event) {
  const data = event.data && typeof event.data === 'object' ? event.data : null;
  let next = { ...state, stage3: data };
  if (!data) return next;
  if (data.error && state.final.status !== 'error') {
    next = withFinal(next, { status: 'error', error: errorOf(data.error), model: data.model ?? state.final.model });
  } else if (!data.error && state.final.status !== 'ready' && typeof data.response === 'string' && data.response) {
    // Older backends sent only stage3_complete; treat it as the ready answer.
    next = withFinal(next, {
      status: 'ready',
      content: data.response,
      latencyMs: numberOrNull(data.latency_ms),
      model: data.model ?? state.final.model,
    });
  }
  return next;
}

function onRunSummary(state, event) {
  return {
    ...state,
    runId: state.runId ?? event.runId ?? null,
    summary: {
      runId: event.runId ?? null,
      costUsd: numberOrNull(event.costUsd),
      tokens: event.tokens && typeof event.tokens === 'object' ? { ...event.tokens } : null,
      failures: Array.isArray(event.failures) ? event.failures : [],
      status: event.status ?? null,
    },
  };
}

function onUnexpectedError(state, event) {
  const message = typeof event.message === 'string' && event.message ? event.message : 'Unexpected error';
  const next = { ...state, phase: 'error', error: message };
  if (state.final.status === 'ready') return next;
  return { ...next, final: { ...state.final, status: 'error', error: { code: null, message } } };
}

const HANDLERS = {
  council_started: onCouncilStarted,
  member_queued: onMemberQueued,
  member_status: onMemberStatus,
  member_streaming: onMemberStreaming,
  member_complete: onMemberComplete,
  member_error: onMemberError,
  stage1_complete: (state, event) => ({
    ...state,
    phase: phaseAtLeast(state.phase, 'stage1'),
    stage1: Array.isArray(event.data) ? event.data : [],
  }),
  stage2_start: onStage2Start,
  stage2_member_status: onStage2MemberStatus,
  stage2_model_complete: onStage2ModelComplete,
  stage2_member_error: onStage2MemberError,
  stage2_member_skipped: onStage2MemberSkipped,
  stage2_skipped: onStage2Skipped,
  stage2_complete: onStage2Complete,
  final_generating: onFinalGenerating,
  final_status: onFinalStatus,
  final_streaming: onFinalStreaming,
  final_ready: onFinalReady,
  final_error: onFinalError,
  stage3_complete: onStage3Complete,
  title_complete: (state, event) => ({ ...state, title: event.data?.title ?? state.title }),
  run_summary: onRunSummary,
  complete: (state) => ({ ...state, phase: state.phase === 'error' ? 'error' : 'complete' }),
  error: onUnexpectedError,
};

/**
 * Applies one §I.6 event. Unknown or malformed events return `state` unchanged (same reference).
 * @param {ReturnType<typeof createRunState>} state
 * @param {{type: string}} event
 * @param {number|null} [now] epoch ms used for status timers
 */
export function reduceCouncilEvent(state, event, now = null) {
  if (!event || typeof event !== 'object' || typeof event.type !== 'string') return state;
  if (!Object.prototype.hasOwnProperty.call(HANDLERS, event.type)) return state;
  return HANDLERS[event.type](state, event, now);
}

/** Folds a whole event list (fixture replay, tests). */
export function reduceCouncilEvents(events, initial = createRunState(), now = null) {
  return events.reduce((state, event) => reduceCouncilEvent(state, event, now), initial);
}

export {
  buildAssistantMessage,
  isRunFinished,
  memberList,
  rankerList,
  runCostUsd,
} from './councilSelectors.js';

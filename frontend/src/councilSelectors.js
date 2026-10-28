/**
 * Read-only selectors over the councilEvents.js run state, plus the conversion of a finished run
 * into the persisted assistant-message shape (§I.7). Pure, plain JS (runs under `node --test`).
 */

export function memberList(state) {
  return state.memberOrder.map((id) => state.members[id]);
}

export function rankerList(state) {
  return state.stage2.rankerOrder.map((id) => state.stage2.rankers[id]);
}

export function isRunFinished(state) {
  return state.phase === 'complete' || state.phase === 'error';
}

/** Sum of member, ranker and chairman costs is only known from run_summary; until then use members + final. */
export function runCostUsd(state) {
  if (state.summary && state.summary.costUsd !== null) return state.summary.costUsd;
  let total = 0;
  let known = false;
  for (const m of memberList(state)) {
    if (m.costUsd !== null) {
      total += m.costUsd;
      known = true;
    }
  }
  if (state.final.costUsd !== null) {
    total += state.final.costUsd;
    known = true;
  }
  return known ? total : null;
}

function snapshotOf(state) {
  if (!state.chairman && state.memberOrder.length === 0) return null;
  const chair = state.chairman;
  return {
    run_id: state.runId,
    members: memberList(state).map((m) => ({
      id: m.memberId,
      display_name: m.displayName,
      kind: m.kind,
      provider_id: m.providerId,
      node_id: m.nodeId,
      can_rank: m.canRank,
    })),
    chairman: chair
      ? { id: chair.modelId, display_name: chair.displayName, kind: chair.kind, provider_id: chair.providerId, node_id: chair.nodeId }
      : null,
  };
}

/**
 * Builds the assistant message (§I.7 shape) from a finished run so the UI can show the turn
 * immediately, before the persisted conversation is reloaded from the backend.
 */
export function buildAssistantMessage(state) {
  const { final, stage2, summary } = state;
  let stage3 = state.stage3;
  if (!stage3 && final.status === 'ready') {
    stage3 = { model: final.model, response: final.content, latency_ms: final.latencyMs, cost_usd: final.costUsd, error: null };
  } else if (!stage3 && final.status === 'error') {
    stage3 = { model: final.model ?? state.chairman?.modelId ?? null, response: '', error: final.error };
  }
  return {
    role: 'assistant',
    stage1: state.stage1 ?? [],
    stage2: stage2.rankings ?? [],
    stage3,
    metadata: stage2.labelToModel
      ? { label_to_model: stage2.labelToModel, aggregate_rankings: stage2.aggregateRankings ?? [] }
      : null,
    council_snapshot: snapshotOf(state),
    failures: summary ? summary.failures : stage2.failures,
    usage: summary
      ? { cost_usd: summary.costUsd, prompt_tokens: summary.tokens?.prompt ?? null, completion_tokens: summary.tokens?.completion ?? null }
      : null,
    status: summary ? summary.status : null,
  };
}

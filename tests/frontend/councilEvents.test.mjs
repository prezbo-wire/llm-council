// Reducer tests for frontend/src/councilEvents.js (§I.6, §K.5): every event type, unknown events,
// partial text on member_error and legacy event shapes. Fixture replays: councilFixtures.test.mjs.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import {
  COUNCIL_EVENT_TYPES,
  buildAssistantMessage,
  isRunFinished,
  rankerList,
  reduceCouncilEvent,
  runCostUsd,
} from '../../frontend/src/councilEvents.js';
import { fixtures, reduceFrozen, root } from './support.mjs';

const started = {
  type: 'council_started',
  runId: 'run_1',
  members: [
    { memberId: 'cloud/a', displayName: 'A', kind: 'cloud', providerId: 'cloud', nodeId: null, canRank: true },
    { memberId: 'spark/b', displayName: 'B', kind: 'local', providerId: null, nodeId: 'spark', canRank: true },
    { memberId: 'ft/c', displayName: 'C', kind: 'custom', providerId: null, nodeId: 'spark', canRank: false },
  ],
  chairman: { modelId: 'cloud/a', displayName: 'A', kind: 'cloud', providerId: 'cloud', nodeId: null },
};

describe('event catalogue', () => {
  test('COUNCIL_EVENT_TYPES matches backend/contracts.py SSE_EVENTS exactly', () => {
    const source = readFileSync(join(root, 'backend', 'contracts.py'), 'utf8');
    const tuple = source.match(/SSE_EVENTS\s*=\s*\(([\s\S]*?)\)/);
    assert.ok(tuple, 'SSE_EVENTS tuple not found');
    const names = [...tuple[1].matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
    assert.deepEqual([...COUNCIL_EVENT_TYPES], names);
  });

  test('every fixture event type is a known §I.6 event', () => {
    for (const fx of fixtures) {
      for (const event of fx.events) assert.ok(COUNCIL_EVENT_TYPES.includes(event.type), `${fx.name}: ${event.type}`);
    }
  });
});

describe('each §I.6 event', () => {
  const base = () => reduceFrozen([started]);

  test('council_started creates members in order, the chairman and the run id', () => {
    const s = base();
    assert.equal(s.runId, 'run_1');
    assert.equal(s.phase, 'stage1');
    assert.deepEqual(s.memberOrder, ['cloud/a', 'spark/b', 'ft/c']);
    assert.equal(s.members['ft/c'].canRank, false);
    assert.equal(s.members['spark/b'].nodeId, 'spark');
    assert.equal(s.members['cloud/a'].status, 'queued');
    assert.deepEqual(s.chairman, started.chairman);
  });

  test('member_queued upserts info without regressing status', () => {
    let s = reduceFrozen([{ type: 'member_queued', memberId: 'x/y', displayName: 'Y', kind: 'cloud', providerId: 'x', nodeId: null, canRank: true }]);
    assert.equal(s.members['x/y'].displayName, 'Y');
    assert.equal(s.members['x/y'].status, 'queued');
    s = reduceFrozen([{ type: 'member_streaming', memberId: 'x/y', deltaText: 'hi' }, { type: 'member_queued', memberId: 'x/y' }], s);
    assert.equal(s.members['x/y'].status, 'streaming');
  });

  test('member_status maps queued/loading/started/retrying/thinking with position, node, attempt, reason', () => {
    let s = reduceFrozen([{ type: 'member_status', memberId: 'spark/b', state: 'queued', position: 2, nodeId: 'spark', reason: 'memory' }], base(), 10);
    assert.equal(s.members['spark/b'].status, 'queued');
    assert.equal(s.members['spark/b'].position, 2);
    assert.equal(s.members['spark/b'].reason, 'memory');
    s = reduceFrozen([{ type: 'member_status', memberId: 'spark/b', state: 'loading', nodeId: 'spark' }], s, 20);
    assert.equal(s.members['spark/b'].status, 'loading');
    assert.equal(s.members['spark/b'].since, 20);
    assert.equal(s.members['spark/b'].position, null);
    s = reduceFrozen([{ type: 'member_status', memberId: 'spark/b', state: 'started', attempt: 1 }], s, 30);
    s = reduceFrozen([{ type: 'member_status', memberId: 'spark/b', state: 'retrying', attempt: 2, reason: 'upstream_rate_limited' }], s, 40);
    assert.equal(s.members['spark/b'].status, 'retrying');
    assert.equal(s.members['spark/b'].attempt, 2);
    assert.equal(s.members['spark/b'].reason, 'upstream_rate_limited');
    s = reduceFrozen([{ type: 'member_status', memberId: 'spark/b', state: 'thinking' }], s, 50);
    assert.equal(s.members['spark/b'].status, 'thinking');
    assert.equal(s.members['spark/b'].attempt, 2, 'attempt survives later states');
  });

  test('member_status with an unknown state is ignored', () => {
    const s = base();
    assert.equal(reduceCouncilEvent(s, { type: 'member_status', memberId: 'cloud/a', state: 'teleporting' }), s);
  });

  test('member_streaming accumulates text; thinking never downgrades a streaming card', () => {
    let s = reduceFrozen(
      [
        { type: 'member_streaming', memberId: 'cloud/a', deltaText: 'Hello ' },
        { type: 'member_streaming', memberId: 'cloud/a', deltaText: 'world' },
        { type: 'member_status', memberId: 'cloud/a', state: 'thinking' },
      ],
      base(),
    );
    assert.equal(s.members['cloud/a'].status, 'streaming');
    assert.equal(s.members['cloud/a'].progressText, 'Hello world');
  });

  test('member_complete stores response, latency, ttft, cost and usage', () => {
    const s = reduceFrozen(
      [{ type: 'member_complete', memberId: 'cloud/a', response: 'Done.', latencyMs: 900, ttftMs: 100, costUsd: 0.0123, usage: { promptTokens: 1000, completionTokens: 400, reasoningTokens: 0 } }],
      base(),
    );
    const m = s.members['cloud/a'];
    assert.equal(m.status, 'done');
    assert.equal(m.progressText, 'Done.');
    assert.equal(m.latencyMs, 900);
    assert.equal(m.ttftMs, 100);
    assert.equal(m.costUsd, 0.0123);
    assert.deepEqual(m.usage, { promptTokens: 1000, completionTokens: 400, reasoningTokens: 0 });
  });

  test('member_error keeps partial text and records code/message/retryable', () => {
    const s = reduceFrozen(
      [
        { type: 'member_streaming', memberId: 'spark/b', deltaText: 'Partial ' },
        { type: 'member_error', memberId: 'spark/b', code: 'upstream_timeout', message: 'no data for 120s', retryable: true },
      ],
      base(),
    );
    const m = s.members['spark/b'];
    assert.equal(m.status, 'error');
    assert.equal(m.progressText, 'Partial ');
    assert.deepEqual(m.error, { code: 'upstream_timeout', message: 'no data for 120s', retryable: true });
  });

  test('member_error never overwrites a done card; later deltas never revive an errored one', () => {
    let s = reduceFrozen([{ type: 'member_complete', memberId: 'cloud/a', response: 'ok', latencyMs: 1 }], base());
    const done = s;
    s = reduceCouncilEvent(s, { type: 'member_error', memberId: 'cloud/a', code: 'internal', message: 'x' });
    assert.equal(s, done);
    s = reduceFrozen([{ type: 'member_error', memberId: 'spark/b', code: 'circuit_open', message: 'open' }], s);
    const errored = s;
    assert.equal(reduceCouncilEvent(s, { type: 'member_streaming', memberId: 'spark/b', deltaText: 'late' }), errored);
    assert.equal(reduceCouncilEvent(s, { type: 'member_status', memberId: 'spark/b', state: 'started' }), errored);
  });

  test('stage1_complete stores the legacy Stage1Entry list', () => {
    const data = [{ model: 'cloud/a', response: 'r', latency_ms: 1, cost_usd: 0 }];
    assert.deepEqual(reduceFrozen([{ type: 'stage1_complete', data }], base()).stage1, data);
  });

  test('stage2_start shows Stage-1 successes with canRank as pending rankers', () => {
    const s = reduceFrozen(
      [
        { type: 'member_complete', memberId: 'cloud/a', response: 'a' },
        { type: 'member_complete', memberId: 'ft/c', response: 'c' },
        { type: 'member_error', memberId: 'spark/b', code: 'x', message: 'y' },
        { type: 'stage2_start' },
      ],
      base(),
    );
    assert.equal(s.phase, 'stage2');
    assert.equal(s.stage2.status, 'running');
    assert.deepEqual(rankerList(s).map((r) => [r.memberId, r.status]), [['cloud/a', 'pending']]);
  });

  test('stage2_member_status / model_complete / member_error / member_skipped drive ranker rows', () => {
    const s = reduceFrozen(
      [
        { type: 'stage2_start' },
        { type: 'stage2_member_status', memberId: 'cloud/a', state: 'queued', position: 1 },
        { type: 'stage2_member_status', memberId: 'spark/b', state: 'loading' },
        { type: 'stage2_member_status', memberId: 'cloud/a', state: 'started' },
        { type: 'stage2_member_status', memberId: 'x/retry', state: 'retrying', attempt: 2 },
        { type: 'stage2_model_complete', memberId: 'cloud/a', ranking: ['Response B', 'Response A'] },
        { type: 'stage2_member_error', memberId: 'spark/b', code: 'upstream_error', message: 'ended' },
        { type: 'stage2_member_skipped', memberId: 'ft/c', reason: 'context' },
        { type: 'stage2_member_status', memberId: 'ft/c', state: 'started' },
      ],
      base(),
      5,
    );
    const r = s.stage2.rankers;
    assert.equal(r['cloud/a'].status, 'done');
    assert.deepEqual(r['cloud/a'].ranking, ['Response B', 'Response A']);
    assert.equal(r['spark/b'].status, 'failed');
    assert.deepEqual(r['spark/b'].error, { code: 'upstream_error', message: 'ended' });
    assert.equal(r['ft/c'].status, 'skipped', 'a terminal ranker ignores later status');
    assert.equal(r['ft/c'].reason, 'context');
    assert.equal(r['x/retry'].status, 'retrying');
    assert.equal(r['x/retry'].attempt, 2);
  });

  test('stage2_skipped then stage2_complete with data [] stays skipped', () => {
    const s = reduceFrozen(
      [{ type: 'stage2_start' }, { type: 'stage2_skipped', reason: 'no_rankers' }, { type: 'stage2_complete', data: [], metadata: { label_to_model: {}, aggregate_rankings: [], failures: [] } }],
      base(),
    );
    assert.equal(s.stage2.status, 'skipped');
    assert.equal(s.stage2.skippedReason, 'no_rankers');
    assert.deepEqual(s.stage2.rankings, []);
    assert.deepEqual(rankerList(s), []);
  });

  test('stage2_complete stores rankings and metadata (label_to_model, aggregate_rankings, failures)', () => {
    const metadata = {
      label_to_model: { 'Response A': 'cloud/a' },
      aggregate_rankings: [{ model: 'cloud/a', average_rank: 1, rankings_count: 1 }],
      failures: [{ stage: '2', model: 'spark/b', code: 'x', message: 'y' }],
    };
    const data = [{ model: 'cloud/a', ranking: 'FINAL RANKING:\n1. Response A', parsed_ranking: ['Response A'] }];
    const s = reduceFrozen([{ type: 'stage2_start' }, { type: 'stage2_complete', data, metadata }], base());
    assert.equal(s.stage2.status, 'complete');
    assert.deepEqual(s.stage2.rankings, data);
    assert.deepEqual(s.stage2.labelToModel, metadata.label_to_model);
    assert.deepEqual(s.stage2.aggregateRankings, metadata.aggregate_rankings);
    assert.deepEqual(s.stage2.failures, metadata.failures);
  });

  test('final_generating / final_status / final_streaming / final_ready', () => {
    let s = reduceFrozen([{ type: 'final_generating' }], base(), 1);
    assert.equal(s.phase, 'stage3');
    assert.equal(s.final.status, 'generating');
    assert.equal(s.final.model, 'cloud/a');
    s = reduceFrozen([{ type: 'final_status', state: 'queued', position: 3, nodeId: 'spark' }], s, 2);
    assert.deepEqual([s.final.status, s.final.position, s.final.nodeId], ['queued', 3, 'spark']);
    s = reduceFrozen([{ type: 'final_status', state: 'loading', nodeId: 'spark' }], s, 3);
    assert.deepEqual([s.final.status, s.final.since], ['loading', 3]);
    s = reduceFrozen([{ type: 'final_status', state: 'started' }, { type: 'final_status', state: 'thinking' }], s, 4);
    assert.equal(s.final.status, 'thinking');
    s = reduceFrozen([{ type: 'final_streaming', deltaText: 'Use ' }, { type: 'final_streaming', deltaText: 'SQLite.' }, { type: 'final_status', state: 'thinking' }], s, 5);
    assert.equal(s.final.status, 'streaming');
    assert.equal(s.final.content, 'Use SQLite.');
    s = reduceFrozen([{ type: 'final_ready', content: 'Use SQLite.', latencyMs: 1200, costUsd: 0.01, model: 'cloud/a' }], s, 6);
    assert.deepEqual([s.final.status, s.final.latencyMs, s.final.costUsd, s.final.model], ['ready', 1200, 0.01, 'cloud/a']);
  });

  test('final_error is an error state that keeps the partial draft and is never overwritten by deltas', () => {
    const s = reduceFrozen(
      [
        { type: 'final_generating' },
        { type: 'final_streaming', deltaText: 'Half an ans' },
        { type: 'final_error', code: 'upstream_timeout', message: 'no data' },
        { type: 'final_streaming', deltaText: 'wer' },
      ],
      base(),
    );
    assert.equal(s.final.status, 'error');
    assert.deepEqual(s.final.error, { code: 'upstream_timeout', message: 'no data' });
    assert.equal(s.final.content, 'Half an ans');
  });

  test('stage3_complete stores the entry; an entry with error turns the final panel into an error', () => {
    const entry = { model: 'cloud/a', response: '', error: { code: 'no_responses', message: 'none' } };
    const s = reduceFrozen([{ type: 'final_generating' }, { type: 'stage3_complete', data: entry }], base());
    assert.deepEqual(s.stage3, entry);
    assert.equal(s.final.status, 'error');
    assert.deepEqual(s.final.error, { code: 'no_responses', message: 'none' });
  });

  test('title_complete, run_summary, complete', () => {
    const s = reduceFrozen(
      [
        { type: 'title_complete', data: { title: 'SQLite' } },
        { type: 'run_summary', runId: 'run_1', costUsd: 0.04, tokens: { prompt: 10, completion: 5 }, failures: [], status: 'complete' },
        { type: 'complete' },
      ],
      base(),
    );
    assert.equal(s.title, 'SQLite');
    assert.deepEqual(s.summary, { runId: 'run_1', costUsd: 0.04, tokens: { prompt: 10, completion: 5 }, failures: [], status: 'complete' });
    assert.equal(s.phase, 'complete');
    assert.ok(isRunFinished(s));
    assert.equal(runCostUsd(s), 0.04);
  });

  test('error (unexpected exception) ends the run and marks a pending final panel as failed', () => {
    let s = reduceFrozen([{ type: 'final_generating' }, { type: 'error', message: 'boom' }], base());
    assert.equal(s.phase, 'error');
    assert.equal(s.error, 'boom');
    assert.equal(s.final.status, 'error');
    assert.equal(s.final.error.message, 'boom');
    s = reduceFrozen([{ type: 'complete' }], s);
    assert.equal(s.phase, 'error', 'complete after error keeps the error');
    const ready = reduceFrozen([{ type: 'final_ready', content: 'ok' }, { type: 'error', message: 'late' }], base());
    assert.equal(ready.final.status, 'ready', 'a ready answer is kept');
  });

  test('every event type is handled (changes state for a meaningful payload)', () => {
    const samples = {
      council_started: started,
      member_queued: { type: 'member_queued', memberId: 'n/new' },
      member_status: { type: 'member_status', memberId: 'cloud/a', state: 'loading' },
      member_streaming: { type: 'member_streaming', memberId: 'cloud/a', deltaText: 'x' },
      member_complete: { type: 'member_complete', memberId: 'cloud/a', response: 'x' },
      member_error: { type: 'member_error', memberId: 'cloud/a', code: 'c', message: 'm' },
      stage1_complete: { type: 'stage1_complete', data: [] },
      stage2_start: { type: 'stage2_start' },
      stage2_member_status: { type: 'stage2_member_status', memberId: 'cloud/a', state: 'started' },
      stage2_model_complete: { type: 'stage2_model_complete', memberId: 'cloud/a', ranking: [] },
      stage2_member_error: { type: 'stage2_member_error', memberId: 'cloud/a', code: 'c', message: 'm' },
      stage2_member_skipped: { type: 'stage2_member_skipped', memberId: 'cloud/a', reason: 'context' },
      stage2_skipped: { type: 'stage2_skipped', reason: 'fewer_than_two_responses' },
      stage2_complete: { type: 'stage2_complete', data: [], metadata: {} },
      final_generating: { type: 'final_generating' },
      final_status: { type: 'final_status', state: 'loading' },
      final_streaming: { type: 'final_streaming', deltaText: 'x' },
      final_ready: { type: 'final_ready', content: 'x' },
      final_error: { type: 'final_error', code: 'c', message: 'm' },
      stage3_complete: { type: 'stage3_complete', data: { model: 'cloud/a', response: 'x' } },
      title_complete: { type: 'title_complete', data: { title: 't' } },
      run_summary: { type: 'run_summary', runId: 'r', costUsd: 0, tokens: { prompt: 0, completion: 0 }, failures: [], status: 'complete' },
      complete: { type: 'complete' },
      error: { type: 'error', message: 'm' },
    };
    assert.deepEqual(Object.keys(samples).sort(), [...COUNCIL_EVENT_TYPES].sort());
    for (const type of COUNCIL_EVENT_TYPES) {
      const before = base();
      const after = reduceCouncilEvent(before, samples[type], 1);
      assert.notEqual(after, before, `${type} should produce a new state`);
    }
  });
});

describe('robustness', () => {
  test('unknown events are ignored (same state reference)', () => {
    const s = reduceFrozen([started]);
    for (const event of [{ type: 'mystery' }, { type: 'toString' }, { type: '__proto__' }, { type: 42 }, null, undefined, 'member_complete', {}]) {
      assert.equal(reduceCouncilEvent(s, event), s);
    }
  });

  test('events for members without council_started still reduce (pre-council_started backends)', () => {
    const s = reduceFrozen([{ type: 'member_streaming', memberId: 'x/y', deltaText: 'a' }]);
    assert.deepEqual(s.memberOrder, ['x/y']);
    assert.equal(s.members['x/y'].displayName, 'x/y');
    assert.equal(reduceCouncilEvent(s, { type: 'member_streaming', deltaText: 'no id' }), s);
  });

  test('legacy event shapes (pre-Mese v2 stream) still reduce to a finished turn', () => {
    const legacy = [
      { type: 'member_queued', memberId: 'openai/gpt-5.1', displayName: 'gpt-5.1' },
      { type: 'member_queued', memberId: 'google/gemini-3-pro-preview', displayName: 'gemini-3-pro-preview' },
      { type: 'member_streaming', memberId: 'openai/gpt-5.1', deltaText: 'WAL ' },
      { type: 'member_complete', memberId: 'openai/gpt-5.1', response: 'WAL appends first.', latencyMs: 5400 },
      { type: 'member_error', memberId: 'google/gemini-3-pro-preview', message: 'Failed to respond' },
      { type: 'stage1_complete', data: [{ model: 'openai/gpt-5.1', response: 'WAL appends first.' }] },
      { type: 'stage2_start' },
      { type: 'stage2_model_complete', memberId: 'openai/gpt-5.1', ranking: 'FINAL RANKING:\n1. Response A' },
      { type: 'stage2_complete', data: [{ model: 'openai/gpt-5.1', ranking: 'r', parsed_ranking: ['Response A'] }], metadata: { label_to_model: { 'Response A': 'openai/gpt-5.1' }, aggregate_rankings: [] } },
      { type: 'final_generating' },
      { type: 'final_streaming', deltaText: 'Final' },
      { type: 'final_ready', content: 'Final answer', latencyMs: 3000 },
      { type: 'stage3_complete', data: { model: 'openai/gpt-5.1', response: 'Final answer' } },
      { type: 'title_complete', data: { title: 'WAL' } },
      { type: 'complete' },
    ];
    const s = reduceFrozen(legacy);
    assert.equal(s.phase, 'complete');
    assert.equal(s.members['openai/gpt-5.1'].status, 'done');
    assert.equal(s.members['openai/gpt-5.1'].costUsd, null);
    assert.equal(s.members['google/gemini-3-pro-preview'].status, 'error');
    assert.deepEqual(s.members['google/gemini-3-pro-preview'].error, { code: null, message: 'Failed to respond', retryable: false });
    assert.equal(s.stage2.rankers['openai/gpt-5.1'].ranking, 'FINAL RANKING:\n1. Response A');
    assert.deepEqual(s.stage2.failures, []);
    assert.equal(s.final.status, 'ready');
    assert.equal(s.final.content, 'Final answer');
    assert.equal(s.title, 'WAL');
    const msg = buildAssistantMessage(s);
    assert.deepEqual(msg.stage3, { model: 'openai/gpt-5.1', response: 'Final answer' });
    assert.equal(msg.usage, null);
  });

  test('the turn built without stage3_complete keeps the chairman cost (COUNCIL-8)', () => {
    const s = reduceFrozen([started, { type: 'final_generating' }, { type: 'final_ready', content: 'Done', latencyMs: 900, costUsd: 0.5, model: 'cloud/a' }]);
    assert.deepEqual(buildAssistantMessage(s).stage3, { model: 'cloud/a', response: 'Done', latency_ms: 900, cost_usd: 0.5, error: null });
  });

  test('stage3_complete alone (no final_ready) still produces a ready answer', () => {
    const s = reduceFrozen([{ type: 'final_generating' }, { type: 'stage3_complete', data: { model: 'a/b', response: 'Answer', latency_ms: 10 } }]);
    assert.deepEqual([s.final.status, s.final.content, s.final.latencyMs], ['ready', 'Answer', 10]);
  });

  test('a second council_started starts a fresh run', () => {
    const s = reduceFrozen([started, { type: 'member_complete', memberId: 'cloud/a', response: 'x' }, { ...started, runId: 'run_2' }]);
    assert.equal(s.runId, 'run_2');
    assert.equal(s.members['cloud/a'].status, 'queued');
  });
});

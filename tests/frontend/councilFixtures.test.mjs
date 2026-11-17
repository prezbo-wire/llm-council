// Replays every WP0 frontend fixture (frontend/src/dev/fixtures/*.json) through the
// councilEvents.js reducer and checks the outcome each event sequence describes (§K.5).
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  buildAssistantMessage,
  createRunState,
  memberList,
  reduceCouncilEvents,
  runCostUsd,
} from '../../frontend/src/councilEvents.js';
import { fixtures, reduceFrozen } from './support.mjs';

describe('WP0 fixture sequences', () => {
  test('there are fixtures to replay', () => {
    assert.ok(fixtures.length >= 6, `found ${fixtures.length}`);
  });

  for (const fx of fixtures) {
    test(`${fx.name}: reduces to the outcome its events describe`, () => {
      const s = reduceFrozen(fx.events);
      const ofType = (type) => fx.events.filter((e) => e.type === type);

      assert.equal(s.phase, 'complete');
      assert.equal(s.runId, ofType('council_started')[0].runId);
      assert.deepEqual(s.memberOrder, fx.council.members);
      assert.equal(s.chairman.modelId, fx.council.chairman);

      for (const e of ofType('member_complete')) {
        const m = s.members[e.memberId];
        assert.equal(m.status, 'done', e.memberId);
        assert.equal(m.progressText, e.response);
        assert.equal(m.costUsd, e.costUsd);
        assert.deepEqual(m.usage, e.usage);
      }
      for (const e of ofType('member_error')) {
        const m = s.members[e.memberId];
        assert.equal(m.status, 'error', e.memberId);
        assert.deepEqual(m.error, { code: e.code, message: e.message, retryable: e.retryable });
        const streamed = fx.events
          .filter((x) => x.type === 'member_streaming' && x.memberId === e.memberId)
          .map((x) => x.deltaText)
          .join('');
        assert.equal(m.progressText, streamed, 'partial text is kept');
      }
      assert.ok(memberList(s).every((m) => m.status === 'done' || m.status === 'error'));

      assert.deepEqual(s.stage1, ofType('stage1_complete')[0].data);
      const s2 = ofType('stage2_complete')[0];
      assert.deepEqual(s.stage2.rankings, s2.data);
      if (ofType('stage2_skipped').length) {
        assert.equal(s.stage2.status, 'skipped');
        assert.equal(s.stage2.skippedReason, ofType('stage2_skipped')[0].reason);
      } else {
        assert.equal(s.stage2.status, 'complete');
        assert.deepEqual(s.stage2.labelToModel, s2.metadata.label_to_model);
      }
      for (const e of ofType('stage2_model_complete')) assert.equal(s.stage2.rankers[e.memberId].status, 'done');
      for (const e of ofType('stage2_member_error')) assert.equal(s.stage2.rankers[e.memberId].status, 'failed');
      for (const e of ofType('stage2_member_skipped')) assert.equal(s.stage2.rankers[e.memberId].status, 'skipped');
      for (const id of fx.council.members) {
        if (s.members[id].canRank === false) assert.equal(s.stage2.rankers[id], undefined, `${id} is Stage 1 only`);
      }

      const s3 = ofType('stage3_complete')[0].data;
      assert.deepEqual(s.stage3, s3);
      if (ofType('final_error').length) {
        assert.equal(s.final.status, 'error');
        assert.equal(s.final.error.code, ofType('final_error')[0].code);
      } else {
        assert.equal(s.final.status, 'ready');
        assert.equal(s.final.content, ofType('final_ready')[0].content);
      }

      const summary = ofType('run_summary')[0];
      assert.equal(s.summary.status, summary.status);
      assert.deepEqual(s.summary.failures, summary.failures);
      assert.equal(runCostUsd(s), summary.costUsd);
      const title = ofType('title_complete')[0];
      assert.equal(s.title, title ? title.data.title : null);

      const msg = buildAssistantMessage(s);
      assert.equal(msg.role, 'assistant');
      assert.deepEqual(msg.stage1, s.stage1);
      assert.deepEqual(msg.stage2, s2.data);
      assert.deepEqual(msg.stage3, s3);
      assert.deepEqual(msg.failures, summary.failures);
      assert.equal(msg.status, summary.status);
      assert.equal(msg.usage.cost_usd, summary.costUsd);
      assert.deepEqual(msg.council_snapshot.members.map((m) => m.id), fx.council.members);
      assert.equal(msg.council_snapshot.chairman.id, fx.council.chairman);
    });
  }

  test('all-local-queue: queue positions, loading timer, retry attempt and Stage-1-only fine-tune', () => {
    const fx = fixtures.find((f) => f.name === 'all-local-queue');
    const at = (n) => reduceCouncilEvents(fx.events.slice(0, n), createRunState(), 777);
    const idx = (pred) => fx.events.findIndex(pred) + 1;
    let s = at(idx((e) => e.type === 'member_status' && e.memberId === 'ft/legal-qwen-v3' && e.state === 'queued'));
    assert.deepEqual([s.members['ft/legal-qwen-v3'].status, s.members['ft/legal-qwen-v3'].position, s.members['ft/legal-qwen-v3'].nodeId], ['queued', 2, 'spark']);
    s = at(idx((e) => e.type === 'member_status' && e.memberId === 'spark/gpt-oss:120b' && e.state === 'loading'));
    assert.deepEqual([s.members['spark/gpt-oss:120b'].status, s.members['spark/gpt-oss:120b'].since], ['loading', 777]);
    s = at(idx((e) => e.type === 'member_status' && e.memberId === 'ft/legal-qwen-v3' && e.position === 1));
    assert.equal(s.members['ft/legal-qwen-v3'].position, 1, 'queue position moved up');
    assert.equal(s.members['ft/legal-qwen-v3'].status, 'queued');
    s = at(idx((e) => e.type === 'member_status' && e.state === 'retrying'));
    assert.deepEqual([s.members['ft/legal-qwen-v3'].status, s.members['ft/legal-qwen-v3'].attempt], ['retrying', 2]);
    s = at(idx((e) => e.type === 'stage2_member_status' && e.state === 'queued'));
    assert.equal(s.stage2.rankers['spark/gpt-oss:120b'].status, 'queued');
    assert.deepEqual(s.stage2.rankerOrder, ['spark/qwen3.8:27b', 'spark/gpt-oss:120b']);
  });

  test('final-error: the chairman failure yields stage3.error, no fake text, partial run', () => {
    const fx = fixtures.find((f) => f.name === 'final-error');
    const s = reduceFrozen(fx.events);
    assert.equal(s.stage3.response, '');
    assert.equal(s.stage3.error.code, 'upstream_timeout');
    assert.equal(buildAssistantMessage(s).stage3.response, '');
    assert.equal(s.summary.status, 'partial');
  });
});

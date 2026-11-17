// ChatGPT-plan models in the UI (Mese spec §16.3 item 7, §1.1 "Usage UI"): the Plan badge, the picker's
// "Using ChatGPT plan · Manage usage" note, the plan_limit_exceeded card, "$0 (plan)" costs and plan reasons.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import {
  MANAGE_USAGE,
  MANAGE_USAGE_URL,
  PLAN_LIMIT_DETAIL,
  PLAN_LIMIT_TITLE,
  PLAN_NOTE,
  isPlanBilled,
} from '../../frontend/src/council/billing.js';
import { badgeFor, describeModel, indexCatalog, planUsageNote, snapshotHints, usesPlan } from '../../frontend/src/council/models.js';
import {
  errorActionFor,
  formatCost,
  formatModelPrice,
  formatUsageFooter,
  humanizeError,
  unavailableText,
} from '../../frontend/src/council/format.js';
import { buildAssistantMessage } from '../../frontend/src/councilEvents.js';
import { REPLAY_CATALOG } from '../../frontend/src/dev/replayData.js';
import { reduceFrozen, root } from './support.mjs';

const PLAN = 'chatgpt/gpt-6.1-sol';
const API_TWIN = 'openai/gpt-6.1-sol';
const index = indexCatalog(REPLAY_CATALOG);
const src = (...parts) => readFileSync(join(root, 'frontend', 'src', ...parts), 'utf8');

describe('billing and the Plan badge', () => {
  test('the catalog billing decides; the chatgpt/ prefix only when Mese sent none', () => {
    assert.equal(index.get(PLAN).billing, 'chatgpt_plan');
    assert.equal(isPlanBilled(index.get(PLAN)), true);
    assert.equal(isPlanBilled(index.get(API_TWIN)), false);
    assert.equal(isPlanBilled({ id: 'chatgpt/gpt-7', billing: null }), true, 'an older Mese or an id that left the catalog');
    assert.equal(isPlanBilled({ id: 'chatgpt/odd', billing: 'api_key' }), false, "Mese's billing wins over the id");
    assert.equal(isPlanBilled({ id: 'spark/qwen3.8:27b', billing: 'local' }), false);
    assert.equal(isPlanBilled(null), false);
    assert.equal(REPLAY_CATALOG.filter(isPlanBilled).map((m) => m.id).join(), PLAN);
  });

  test('ModelBadge shows "Plan" for billing chatgpt_plan, from the catalog, a hint or a snapshot', () => {
    const live = badgeFor(describeModel(PLAN, { index }));
    assert.deepEqual([live.variant, live.label], ['plan', 'Plan']);
    assert.match(live.title, /ChatGPT plan/);
    assert.equal(badgeFor(describeModel(API_TWIN, { index })).label, 'Cloud · openai', 'the API-key twin stays Cloud');
    const hint = { displayName: 'GPT-7', kind: 'cloud', providerId: 'chatgpt', billing: 'chatgpt_plan' };
    assert.equal(badgeFor(describeModel('chatgpt/gpt-7', { index, hint })).label, 'Plan');
    const hints = snapshotHints({
      members: [{ id: 'chatgpt/gpt-7', display_name: 'GPT-7', kind: 'cloud', provider_id: 'chatgpt', billing: 'chatgpt_plan' }],
      chairman: { id: 'anthropic/y', display_name: 'Y', kind: 'cloud', provider_id: 'anthropic', billing: 'api_key' },
    });
    assert.equal(hints.get('chatgpt/gpt-7').billing, 'chatgpt_plan');
    assert.equal(badgeFor(describeModel('anthropic/y', { index, hint: hints.get('anthropic/y') })).variant, 'cloud');
    assert.equal(badgeFor(describeModel('chatgpt/gone', { index })).variant, 'legacy', 'nothing describes it: still Legacy');
    assert.ok(src('components', 'ModelBadge.css').includes('.model-badge-plan {'));
    assert.ok(src('components', 'CouncilPicker', 'CouncilPicker.css').includes('.chip-plan .chip-dot {'));
  });

  test('the picker row prices a plan model as "ChatGPT plan", never "$0 / $0"', () => {
    const price = formatModelPrice(index.get(PLAN));
    assert.deepEqual([price.text, price.tone], ['ChatGPT plan', 'plan']);
    assert.match(formatModelPrice(index.get(API_TWIN)).text, /^\$2 \/ \$10/, 'the API-key twin keeps its USD price');
    assert.ok(src('components', 'CouncilPicker', 'ModelRow.css').includes('.model-row-price.price-plan {'));
  });
});

describe('"Using ChatGPT plan · Manage usage" in the picker', () => {
  test('shown while a member or the chairman is plan-billed, with the exact Manage usage link', () => {
    const expected = { text: 'Using ChatGPT plan', action: { label: 'Manage usage', href: 'https://chatgpt.com/settings/usage' } };
    assert.deepEqual(planUsageNote({ members: [PLAN, 'spark/qwen3.8:27b'], chairman: 'anthropic/claude-opus-5-5' }, index), expected);
    assert.deepEqual(planUsageNote({ members: ['spark/qwen3.8:27b'], chairman: PLAN }, index), expected);
    assert.equal(planUsageNote({ members: [API_TWIN], chairman: API_TWIN }, index), null);
    assert.equal(planUsageNote(null, index), null);
    assert.equal(PLAN_NOTE, 'Using ChatGPT plan');
    assert.equal(MANAGE_USAGE_URL, 'https://chatgpt.com/settings/usage');
    assert.equal(usesPlan(['chatgpt/gpt-7'], { index, hints: new Map([['chatgpt/gpt-7', { billing: 'chatgpt_plan' }]]) }), true);
  });

  test('CouncilPicker renders the note with a new-tab Manage usage link', () => {
    const picker = src('components', 'CouncilPicker', 'CouncilPicker.jsx');
    assert.ok(picker.includes('planUsageNote(draft, index)'));
    assert.match(picker, /className="picker-plan"[\s\S]*href=\{planNote\.action\.href\} target="_blank" rel="noopener noreferrer"/);
    assert.ok(src('components', 'CouncilPicker', 'CouncilPicker.css').includes('.picker-plan {'));
  });
});

describe('plan_limit_exceeded', () => {
  test('reads "Usage limit reached / Review your plan or this app\'s limit in ChatGPT settings."', () => {
    const raw = 'ChatGPT plan usage limit reached; Mese pauses chatgpt/* until it is resumed (request id req_1)';
    assert.deepEqual(humanizeError('plan_limit_exceeded', raw), {
      title: 'Usage limit reached',
      detail: "Review your plan or this app's limit in ChatGPT settings.",
      code: 'plan_limit_exceeded',
    });
    assert.deepEqual([PLAN_LIMIT_TITLE, PLAN_LIMIT_DETAIL], ['Usage limit reached', "Review your plan or this app's limit in ChatGPT settings."]);
  });

  test('Manage usage is its primary action; other errors have none', () => {
    assert.deepEqual(errorActionFor('plan_limit_exceeded'), { label: 'Manage usage', href: 'https://chatgpt.com/settings/usage' });
    assert.equal(errorActionFor('plan_limit_exceeded'), MANAGE_USAGE);
    for (const code of ['circuit_open', 'upstream_rate_limited', 'upstream_auth_error', null]) assert.equal(errorActionFor(code), null);
    const action = src('components', 'ErrorAction.jsx');
    assert.ok(action.includes('target="_blank"') && action.includes('rel="noopener noreferrer"'));
    assert.ok(src('components', 'ErrorAction.css').includes('.error-action-primary {'));
  });

  test('the member card, the chairman panel, stored turns and ranker rows render the action', () => {
    const primary = [['CouncilBoard', 'CouncilMemberCard.jsx'], ['FinalDecisionPanel', 'FinalDecisionPanel.jsx'], ['Stage3.jsx']];
    for (const file of primary) assert.match(src('components', ...file), /<ErrorAction code=\{code\} \/>/, file.join('/'));
    assert.match(src('components', 'Stage2Collapsible', 'RankerRows.jsx'), /<ErrorAction code=\{ranker\.error\?\.code\} compact \/>/);
    assert.match(src('components', 'AssistantMessage.jsx'), /<ErrorAction code=\{failure\.code\} compact \/>/);
  });
});

describe('plan cost shows as "$0 (plan)"', () => {
  test('formatCost and the card footer', () => {
    assert.equal(formatCost(0, { plan: true }), '$0 (plan)');
    assert.equal(formatCost(0.0123, { plan: true }), '$0.0123 + $0 (plan)', 'a run with API-key calls too');
    assert.equal(formatCost(0), '$0');
    assert.equal(formatCost(null, { plan: true }), null);
    const usage = { promptTokens: 1000, completionTokens: 400 };
    assert.equal(formatUsageFooter({ costUsd: 0, usage, plan: true }), '$0 (plan) · 1.4k tok');
  });

  test('the live run summary, card, chairman and the stored Stage 1, Stage 3 and turn totals pass `plan`', () => {
    const uses = [
      [['CouncilBoard', 'CouncilBoard.jsx'], /formatCost\(runCostUsd\(run\), \{ plan: anyPlan\(/],
      [['CouncilBoard', 'CouncilMemberCard.jsx'], /plan=\{isPlanBilled\(info\)\}/],
      [['FinalDecisionPanel', 'FinalDecisionPanel.jsx'], /formatCost\(costUsd, \{ plan:/],
      [['Stage1.jsx'], /formatCost\(current\.cost_usd, \{ plan: isPlan\(current\.model\) \}\)/],
      [['Stage3.jsx'], /formatCost\(chairmanCostUsd\(finalResponse, usage\), \{ plan:/],
      [['AssistantMessage.jsx'], /formatCost\(usage\.cost_usd, \{ plan: anyPlan\(ids\) \}\)/],
    ];
    for (const [file, pattern] of uses) assert.match(src('components', ...file), pattern, file.join('/'));
  });
});

describe('plan unavailable reasons in the picker', () => {
  test('plan_limited and plan_unavailable point at Mese admin; no_credential on a plan model means "connect ChatGPT"', () => {
    assert.deepEqual(unavailableText('plan_limited'), { text: 'ChatGPT usage limit reached: paused in Mese', admin: true });
    assert.deepEqual(unavailableText('plan_unavailable'), { text: 'Not available on your ChatGPT plan', admin: true });
    assert.equal(unavailableText('no_credential', index.get(PLAN)).text, 'ChatGPT not connected: Continue with ChatGPT in Mese admin');
    assert.equal(unavailableText('no_credential', index.get(API_TWIN)).text, 'No API key: add it in Mese admin');
    assert.ok(src('components', 'CouncilPicker', 'ModelRow.jsx').includes('unavailableText(model.unavailable_reason, model)'));
  });
});

describe('council_started billing', () => {
  test('members and the chairman keep billing; member_queued (which has none) does not erase it', () => {
    const started = {
      type: 'council_started',
      runId: 'run_p',
      members: [
        { memberId: PLAN, displayName: 'GPT-6.1 Sol (ChatGPT plan)', kind: 'cloud', providerId: 'chatgpt', nodeId: null, canRank: true, billing: 'chatgpt_plan' },
        { memberId: 'spark/b', displayName: 'B', kind: 'local', providerId: null, nodeId: 'spark', canRank: true, billing: 'local' },
      ],
      chairman: { modelId: PLAN, displayName: 'GPT-6.1 Sol (ChatGPT plan)', kind: 'cloud', providerId: 'chatgpt', nodeId: null, billing: 'chatgpt_plan' },
    };
    const queued = { type: 'member_queued', memberId: PLAN, displayName: 'GPT-6.1 Sol (ChatGPT plan)', kind: 'cloud', providerId: 'chatgpt', nodeId: null, canRank: true };
    const s = reduceFrozen([started, queued]);
    assert.equal(s.members[PLAN].billing, 'chatgpt_plan');
    assert.equal(s.members['spark/b'].billing, 'local');
    assert.equal(s.chairman.billing, 'chatgpt_plan');
    const error = { type: 'member_error', memberId: PLAN, code: 'plan_limit_exceeded', message: 'limit', retryable: false };
    const failed = reduceFrozen([error], s).members[PLAN];
    assert.deepEqual([failed.status, failed.error.code], ['error', 'plan_limit_exceeded']);
    const snapshot = buildAssistantMessage(s).council_snapshot;  // the turn shown before the reload keeps billing
    assert.deepEqual(snapshot.members.map((m) => m.billing), ['chatgpt_plan', 'local']);
    assert.equal(snapshot.chairman.billing, 'chatgpt_plan');
  });
});

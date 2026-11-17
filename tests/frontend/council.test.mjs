// Tests for the picker / badge / formatting helpers in frontend/src/council/*.js.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  badgeFor,
  cpuOffloadByModel,
  describeModel,
  indexCatalog,
  loadDotFor,
  matchesFilter,
  matchesSearch,
  planTurnLine,
  residencyHintFor,
  shortModelName,
  snapshotHints,
  sortForPicker,
} from '../../frontend/src/council/models.js';
import {
  chairmanCostUsd,
  describeApiError,
  formatCost,
  formatElapsed,
  formatModelPrice,
  formatTokens,
  formatUsageFooter,
  humanizeError,
  queuedText,
  retryText,
  stage2SkipText,
  turnStatusBadge,
  unavailableText,
} from '../../frontend/src/council/format.js';
import {
  LAST_USED_PRESET_ID,
  MAX_MEMBERS,
  draftFromPreset,
  lastUsedChipState,
  normalizeDraft,
  pickerShouldCommit,
  resolveInitialDraft,
  sameSelection,
  slugifyPresetName,
  toggleChairman,
  toggleMember,
  validateDraft,
  withPresetMatch,
} from '../../frontend/src/council/draft.js';
import {
  FOCUS_RESOURCE_REFRESH_MS,
  healthSignature,
  meseStatus,
  planKey,
  planWarnings,
  resourceRefresh,
} from '../../frontend/src/council/status.js';
import { deAnonymizeText, rankingSummary } from '../../frontend/src/council/rankings.js';
import { appendTurn, endsWithUserMessage } from '../../frontend/src/council/turns.js';
import { REPLAY_CATALOG } from '../../frontend/src/dev/replayData.js';

const catalog = REPLAY_CATALOG;
const index = indexCatalog(catalog);
const get = (id) => index.get(id);
const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, '..', '..', 'frontend', 'src');
const meseFixture = (name) => JSON.parse(readFileSync(join(HERE, '..', 'fixtures', 'mese', name), 'utf8'));
// The replay is Spark-only: the fixture's node_down model on the neutral test host "lab" moves to spark-llama.
const SPARK_LLAMA = { id: 'spark-llama', name: 'DGX Spark · llama.cpp', runtime: 'llamacpp_router', host_id: 'spark' };
const SPARK_ONLY = { 'lab/glimmer:30b': { id: 'spark-llama/glimmer-30b', node: SPARK_LLAMA } };

describe('models', () => {
  test('the dev replay catalog is the CouncilModel projection of WP0 v1_models.json, Spark-only', () => {
    const v1 = meseFixture('v1_models.json');
    const projected = v1.data.map((m) => {
      const x = m.mese;
      const r = x.residency;
      return {
        id: m.id,
        name: x.display_name || m.name,
        kind: x.kind,
        provider: x.provider,
        node: x.node ? { id: x.node.id, name: x.node.name, runtime: x.node.runtime, host_id: x.node.host_id } : null,
        available: x.available,
        unavailable_reason: x.unavailable_reason,
        load_state: x.load_state,
        residency: r ? { effective: r.class, configured: r.configured, est_load_s: r.est_load_s, observed_tok_s: r.observed_tok_s } : null,
        can_rank: x.can_rank,
        can_chair: x.can_chair,
        context_length: m.context_length,
        size_bytes: x.size_bytes,
        price_per_mtok: x.kind === 'cloud'
          ? { input: Math.round(Number(m.pricing.prompt) * 1e12) / 1e6, output: Math.round(Number(m.pricing.completion) * 1e12) / 1e6 }
          : null,
        price_verified: x.price_verified,
        billing: x.billing,
        tags: x.tags,
        lineage: x.lineage ? { name: x.lineage.name, version: x.lineage.version } : null,
        ...SPARK_ONLY[m.id],
      };
    });
    assert.deepEqual(REPLAY_CATALOG, projected);
    assert.ok(REPLAY_CATALOG.every((m) => !m.node || m.node.host_id === 'spark'));
  });

  test('badges: Cloud · provider, Local · node, FT · version, Legacy for ids missing from the catalog', () => {
    assert.equal(badgeFor(describeModel('anthropic/claude-opus-5-5', { index })).label, 'Cloud · anthropic');
    assert.equal(badgeFor(describeModel('spark/qwen3.8:27b', { index })).label, 'Local · spark');
    const ft = badgeFor(describeModel('ft/legal-qwen-v3', { index }));
    assert.deepEqual([ft.variant, ft.label], ['ft', 'FT · v3']);
    const legacy = describeModel('openai/gpt-5.1', { index });
    assert.equal(legacy.legacy, true);
    assert.equal(legacy.displayName, 'gpt-5.1');
    assert.equal(badgeFor(legacy).label, 'Legacy');
  });

  test('no Legacy badge while the catalog is not loaded; hints describe ids the catalog lacks', () => {
    assert.equal(badgeFor(describeModel('openai/gpt-5.1', { index: null })), null);
    const hinted = describeModel('gone/model', { index, hint: { displayName: 'Gone', kind: 'local', nodeId: 'lab' } });
    assert.deepEqual([hinted.legacy, hinted.displayName, badgeFor(hinted).label], [false, 'Gone', 'Local · lab']);
  });

  test('snapshotHints indexes council_snapshot members and chairman', () => {
    const hints = snapshotHints({
      run_id: 'r',
      members: [{ id: 'spark/x', display_name: 'X', kind: 'local', provider_id: null, node_id: 'spark' }],
      chairman: { id: 'anthropic/y', display_name: 'Y', kind: 'cloud', provider_id: 'anthropic', node_id: null },
    });
    assert.equal(hints.get('spark/x').displayName, 'X');
    assert.equal(hints.get('anthropic/y').providerId, 'anthropic');
    assert.equal(snapshotHints(null).size, 0);
  });

  test('Local filter includes fine-tunes; Cloud only cloud; search matches name, node, provider and tags', () => {
    const ids = (filter, q = '') => catalog.filter((m) => matchesFilter(m, filter) && matchesSearch(m, q)).map((m) => m.id);
    assert.ok(ids('local').includes('ft/legal-qwen-v3'));
    assert.ok(ids('local').every((id) => get(id).kind !== 'cloud'));
    assert.ok(ids('cloud').every((id) => get(id).kind === 'cloud'));
    assert.equal(ids('all').length, catalog.length);
    assert.deepEqual(ids('all', 'legal'), ['ft/legal-qwen-v3']);
    assert.deepEqual(ids('cloud', 'anthropic haiku'), ['anthropic/claude-haiku-5-5']);
    assert.ok(ids('all', 'vllm').includes('spark-vllm/Qwen/Qwen3.8-27B'));
    assert.deepEqual(ids('local', 'frontier'), []);
  });

  test('sortForPicker puts available models first and keeps catalog order within a kind', () => {
    const sorted = sortForPicker(catalog);
    const firstUnavailable = sorted.findIndex((m) => !m.available);
    assert.ok(sorted.slice(firstUnavailable).every((m) => !m.available));
    assert.equal(sorted[0].id, 'anthropic/claude-opus-5-5');
  });

  test('load dots for local models only', () => {
    assert.equal(loadDotFor(get('anthropic/claude-opus-5-5')), null);
    assert.equal(loadDotFor(get('spark/qwen3.8:27b')).tone, 'loaded');
    assert.equal(loadDotFor(get('spark/gpt-oss:120b')).tone, 'unloaded');
    assert.equal(loadDotFor(get('spark-llama/glimmer-30b')).tone, 'unknown');
  });

  test('shortModelName', () => {
    assert.equal(shortModelName('spark-vllm/Qwen/Qwen3.8-27B'), 'Qwen/Qwen3.8-27B');
    assert.equal(shortModelName('plain'), 'plain');
  });
});

describe('residency', () => {
  const res = (effective, estLoadS, { configured = effective, tok = null } = {}) => ({ effective, configured, est_load_s: estLoadS, observed_tok_s: tok });
  const model = (residency, load = 'unloaded') => ({ ...get('spark/qwen3.8:27b'), residency, load_state: load });
  const pills = (m, opts) => residencyHintFor(m, opts).map((p) => [p.tone, p.label]);

  test('residencyHintFor: the picker pill table', () => {
    assert.deepEqual(residencyHintFor(get('spark/qwen3.8:27b')),
      [{ tone: 'resident', label: 'Resident', title: 'Always loaded on spark (~39 tok/s recently)' }]);
    const reloading = residencyHintFor(model(res('resident', 21.4)));
    assert.deepEqual([reloading[0].label, reloading[0].title], ['Resident · reloading', 'Reloads in ~21 s (after a large model or a restart)']);
    assert.deepEqual(pills(get('ft/legal-qwen-v3')), [['on-demand', 'On demand · ~17 s']]);
    assert.deepEqual(pills(model(res('on_demand', 0), 'loaded')), [['on-demand', 'On demand · loaded']]);
    assert.deepEqual(pills(model(res('on_demand', null))), [['on-demand', 'On demand']]);
    assert.deepEqual(pills(get('spark/gpt-oss:120b')), [['exclusive', 'Exclusive · ~52 s']]);
    assert.deepEqual(pills(model(res('exclusive', 48.3, { configured: 'on_demand' }))), [['exclusive-auto', 'Exclusive (auto) · ~48 s']]);
    assert.deepEqual(pills(model(res('exclusive', 192, { configured: 'resident' }))), [['exclusive-auto', 'Exclusive (auto) · ~3 min']]);
    const slow = residencyHintFor(model(res('on_demand', 33, { tok: 4.4 })));
    assert.deepEqual(slow.map((p) => p.label), ['On demand · ~33 s', 'Slow · ~4.4 tok/s']);
    assert.equal(slow[1].title, 'A 1,000-token answer takes ~4 min');
    assert.deepEqual(pills(model(res('resident', 0, { tok: 10 }), 'loaded')), [['resident', 'Resident']], '10 tok/s is not slow');
    const offloaded = residencyHintFor(get('spark/qwen3.8:27b'), { cpuOffload: 'Qwen runs partly on the CPU' });
    assert.deepEqual(offloaded.slice(1), [{ tone: 'cpu-offload', label: 'CPU offload', title: 'Qwen runs partly on the CPU' }]);
    assert.deepEqual(residencyHintFor(get('anthropic/claude-opus-5-5')), []);
    assert.deepEqual(residencyHintFor(get('spark-vllm/Qwen/Qwen3.8-27B')), [], 'no residency data: no class pill');
  });

  test('turn line from plan.turn', () => {
    const exclusive = meseFixture('v1_plan_exclusive.json');
    assert.deepEqual(planTurnLine(exclusive), { tone: 'loads', text: 'Local: 1 model load (+~48 s); residents reload ~52 s later' });
    assert.equal(planTurnLine(meseFixture('v1_plan.json')), null, 'no chairman sent, or an older Mese');
    const idle = { loads: 0, load_s: 0, restore_s: 0, stages: [] };
    assert.deepEqual(planTurnLine({ ...exclusive, turn: idle }), { tone: 'none', text: 'Local: no model loads' });
    assert.equal(planTurnLine({ items: [{ kind: 'cloud' }], turn: idle }), null, 'an all-cloud council has no turn line');
    assert.equal(planTurnLine({ items: [], turn: { ...idle, loads: 4, load_s: 192 } }).text, 'Local: 4 model loads (+~3 min)');
    assert.equal(planTurnLine(null), null);
  });

  test('residency warnings keep their codes, render as styled plan-<code> lines; every pill tone is styled', () => {
    const exclusive = meseFixture('v1_plan_exclusive.json');
    const plan = { ...exclusive, warnings: [...exclusive.warnings,
      { code: 'slow', model: 'spark/nemotron:latest', host_id: 'spark', message: 'nemotron:latest decodes at ~4.4 tok/s' },
      { code: 'cpu_offload', model: 'spark/qwen3.8:27b', host_id: 'spark', message: 'Qwen runs partly on the CPU' }] };
    assert.deepEqual(planWarnings(plan).map((w) => w.code), ['exclusive', 'waves', 'slow', 'cpu_offload']);
    assert.deepEqual([...cpuOffloadByModel(plan)], [['spark/qwen3.8:27b', 'Qwen runs partly on the CPU']]);
    const css = (name) => readFileSync(join(SRC, 'components', 'CouncilPicker', name), 'utf8');
    const [picker, row] = [css('CouncilPicker.css'), css('ModelRow.css')];
    for (const code of ['exclusive', 'slow', 'cpu_offload']) assert.ok(picker.includes(`.picker-warning.plan-${code} {`), code);
    for (const tone of ['none', 'loads']) assert.ok(picker.includes(`.picker-turn.turn-${tone} {`), tone);
    const tones = ['resident', 'reloading', 'on-demand', 'exclusive', 'exclusive-auto', 'slow', 'cpu-offload'];
    for (const tone of tones) assert.ok(row.includes(`.residency-pill.pill-${tone} {`), tone);
  });
});

describe('format', () => {
  test('price per 1M: "$4 / $20", "free · local", unverified', () => {
    assert.equal(formatModelPrice(get('anthropic/claude-opus-5-5')).text, '$4 / $20');
    assert.equal(formatModelPrice(get('anthropic/claude-haiku-5-5')).text, '$0.1 / $0.5');
    assert.equal(formatModelPrice(get('spark/qwen3.8:27b')).text, 'free · local');
    assert.equal(formatModelPrice(get('ft/legal-qwen-v3')).text, 'free · local');
    assert.equal(formatModelPrice(get('openai/gpt-6.1-sol')).text, '$2 / $10 · unverified');
    assert.equal(formatModelPrice({ ...get('openai/gpt-6.1-sol'), price_per_mtok: null }).text, 'price unverified');
  });

  test('cost, tokens and the card footer "$0.0123 · 1.4k tok"', () => {
    assert.equal(formatCost(0), '$0');
    assert.equal(formatCost(0.0123), '$0.0123');
    assert.equal(formatCost(0.00412), '$0.00412');
    assert.equal(formatCost(0.00001), '<$0.0001');
    assert.equal(formatCost(1.5), '$1.50');
    assert.equal(formatCost(null), null);
    assert.equal(formatTokens(950), '950');
    assert.equal(formatTokens(1400), '1.4k');
    assert.equal(formatTokens(12345), '12k');
    assert.equal(formatTokens(1_200_000), '1.2M');
    assert.equal(formatUsageFooter({ costUsd: 0.0123, usage: { promptTokens: 1000, completionTokens: 400, reasoningTokens: 50 } }), '$0.0123 · 1.4k tok');
    assert.equal(formatUsageFooter({ costUsd: 0, usage: null }), '$0');
    assert.equal(formatUsageFooter({}), '');
  });

  test('status texts', () => {
    assert.equal(queuedText(2, 'spark'), 'Queued #2 on spark');
    assert.equal(queuedText(null, null), 'Queued');
    assert.equal(retryText(2, 'upstream_rate_limited'), 'Retrying (rate limited by the provider) · attempt 2');
    assert.equal(formatElapsed(12_400), '12 s');
    assert.equal(formatElapsed(75_000), '1 m 15 s');
    assert.equal(stage2SkipText('fewer_than_two_responses'), 'Peer ranking skipped: fewer than two responses');
  });

  test('human error text per code keeps the raw message as detail', () => {
    assert.deepEqual(humanizeError('credential_missing', 'No API key in Mese'), { title: 'No API key in Mese', detail: null, code: 'credential_missing' });
    assert.deepEqual(humanizeError('content_filtered', 'declined by provider (cyber)'), {
      title: 'Declined by provider',
      detail: 'declined by provider (cyber)',
      code: 'content_filtered',
    });
    assert.equal(humanizeError('brand_new_code', 'Something odd').title, 'Something odd');
    assert.equal(humanizeError(null, '').title, 'Failed to respond');
  });

  test('unavailable reasons point at Mese admin where the fix lives', () => {
    assert.deepEqual(unavailableText('no_credential'), { text: 'No API key: add it in Mese admin', admin: true });
    assert.equal(unavailableText('node_down').text, 'Node down');
    assert.equal(unavailableText('locked').text, 'Locked');
    assert.equal(unavailableText('weird').text, 'Unavailable (weird)');
  });

  test('describeApiError lists unknown models', () => {
    assert.equal(describeApiError({ code: 'unknown_models', detail: { bad_ids: ['a/b', 'c/d'] }, message: 'x' }), 'Not in the Mese catalog: a/b, c/d');
    assert.equal(describeApiError({ code: 'mese_unreachable', message: 'connect refused' }), 'Mese is unreachable: connect refused');
  });
});

describe('draft', () => {
  const hybrid = {
    id: 'builtin:hybrid', name: 'Hybrid', members: ['anthropic/claude-sonnet-5-5', 'spark/qwen3.8:27b'],
    chairman: 'anthropic/claude-opus-5-5', builtin: true, unavailable: [],
  };

  /** The backend's builtin:last-used entry (backend/presets.py resolve_last_used). */
  const lastUsedPreset = (members, chairman, source = null) => ({
    id: LAST_USED_PRESET_ID, name: 'Last used', members, chairman, builtin: true, unavailable: [],
    updated_at: '2026-11-17T12:00:00Z', source_preset_id: source, conversation_id: 'c1',
  });
  const myTeam = {
    id: 'my-team', name: 'My team', members: ['anthropic/claude-opus-5-5', 'spark/qwen3.8:27b'],
    chairman: 'anthropic/claude-opus-5-5', builtin: false, unavailable: [],
  };

  test('initial draft: conversation council, then the Last used preset, then builtin:hybrid', () => {
    const conversationCouncil = { members: ['spark/qwen3.8:27b'], chairman: 'spark/gpt-oss:120b', preset_id: null, updated_at: 'x' };
    const lastUsed = lastUsedPreset(['anthropic/claude-opus-5-5'], 'anthropic/claude-opus-5-5');
    assert.deepEqual(resolveInitialDraft({ conversationCouncil, presets: [lastUsed, hybrid] }), {
      draft: { members: ['spark/qwen3.8:27b'], chairman: 'spark/gpt-oss:120b', presetId: null },
      source: 'conversation',
    });
    assert.deepEqual(resolveInitialDraft({ conversationCouncil: null, presets: [lastUsed, hybrid] }), {
      draft: { members: ['anthropic/claude-opus-5-5'], chairman: 'anthropic/claude-opus-5-5', presetId: null },
      source: 'last-used',
    });
    const hybridDraft = { draft: { members: hybrid.members, chairman: hybrid.chairman, presetId: 'builtin:hybrid' }, source: 'preset' };
    assert.deepEqual(resolveInitialDraft({ presets: [lastUsedPreset([], null), hybrid] }), hybridDraft, 'nothing sent yet');
    assert.deepEqual(resolveInitialDraft({ presets: [lastUsedPreset(['anthropic/claude-opus-5-5'], null), hybrid] }), hybridDraft, 'its chair is gone');
    assert.deepEqual(resolveInitialDraft({}), { draft: null, source: null });
  });

  test('withPresetMatch never picks builtin:last-used', () => {
    const lastUsed = lastUsedPreset([...myTeam.members], myTeam.chairman, 'my-team');
    const draft = { members: [...myTeam.members].reverse(), chairman: myTeam.chairman, presetId: null };
    assert.equal(withPresetMatch(draft, [lastUsed, hybrid, myTeam]).presetId, 'my-team');
    assert.equal(withPresetMatch({ ...draft, presetId: LAST_USED_PRESET_ID }, [lastUsed, myTeam]).presetId, 'my-team');
    const onlyLastUsed = lastUsedPreset(['spark/gpt-oss:120b'], 'spark/gpt-oss:120b');
    assert.equal(withPresetMatch({ members: ['spark/gpt-oss:120b'], chairman: 'spark/gpt-oss:120b', presetId: null }, [onlyLastUsed, hybrid]).presetId, null);
  });

  test('applying Last used keeps the source preset only while it still matches', () => {
    const presets = [lastUsedPreset([...myTeam.members], myTeam.chairman, 'my-team'), hybrid, myTeam];
    assert.deepEqual(draftFromPreset(presets[0], presets), { members: myTeam.members, chairman: myTeam.chairman, presetId: 'my-team' });
    const edited = { ...myTeam, members: ['spark/qwen3.8:27b'] }; // "My team" changed since that send
    assert.equal(draftFromPreset(presets[0], [presets[0], hybrid, edited]).presetId, null);
    const asHybrid = lastUsedPreset([...hybrid.members], hybrid.chairman, 'my-team');
    assert.equal(draftFromPreset(asHybrid, [asHybrid, hybrid, myTeam]).presetId, 'builtin:hybrid', 'another exact match');
    assert.equal(draftFromPreset(lastUsedPreset(['a/b'], 'a/b'), []).presetId, null);
    assert.equal(draftFromPreset(hybrid).presetId, 'builtin:hybrid');
  });

  test('lastUsedChipState: disabled when empty or already the current council', () => {
    const lastUsed = lastUsedPreset([...myTeam.members], myTeam.chairman, 'my-team');
    assert.deepEqual(lastUsedChipState(lastUsedPreset([], null), null), { disabled: true, reason: 'Nothing sent yet' });
    const current = { members: [...myTeam.members].reverse(), chairman: myTeam.chairman, presetId: 'my-team' };
    assert.deepEqual(lastUsedChipState(lastUsed, current), { disabled: true, reason: 'Already the current council' });
    assert.deepEqual(lastUsedChipState(lastUsed, { ...current, chairman: 'spark/qwen3.8:27b' }), { disabled: false, reason: null });
    assert.deepEqual(lastUsedChipState(lastUsed, null), { disabled: false, reason: null });
  });

  test('valid: 1–8 members plus a chairman that can chair', () => {
    const ok = validateDraft({ members: ['spark/qwen3.8:27b', 'anthropic/claude-opus-5-5'], chairman: 'anthropic/claude-opus-5-5', presetId: null }, index);
    assert.equal(ok.valid, true);
    assert.equal(validateDraft({ members: [], chairman: 'anthropic/claude-opus-5-5' }, index).valid, false);
    assert.equal(validateDraft({ members: ['spark/qwen3.8:27b'], chairman: null }, index).valid, false);
    const nine = Array.from({ length: 9 }, (_, i) => `x/m${i}`);
    assert.ok(validateDraft({ members: nine, chairman: 'x/m0' }, null).problems.includes('At most 8 members'));
    assert.equal(validateDraft({ members: ['a/b', 'a/b'], chairman: 'a/b' }, null).valid, false);
    assert.equal(validateDraft({ members: ['Bad Id'], chairman: 'a/b' }, null).valid, false);
    assert.equal(validateDraft(null, index).valid, false);
  });

  test('chair limited to can_chair; unknown ids rejected; unavailable ids allowed but reported', () => {
    const ft = validateDraft({ members: ['spark/qwen3.8:27b'], chairman: 'ft/legal-qwen-v3' }, index);
    assert.equal(ft.valid, false);
    assert.ok(ft.problems.includes('Legal Qwen v3 cannot chair'));
    const unknown = validateDraft({ members: ['openai/gpt-5.1'], chairman: 'anthropic/claude-opus-5-5' }, index);
    assert.deepEqual([unknown.valid, unknown.unknown], [false, ['openai/gpt-5.1']]);
    const unavailable = validateDraft({ members: ['xai/grok-4.7', 'spark/qwen3.8:27b'], chairman: 'anthropic/claude-opus-5-5' }, index);
    assert.deepEqual([unavailable.valid, unavailable.unavailable], [true, ['xai/grok-4.7']]);
    assert.equal(validateDraft({ members: ['openai/gpt-5.1'], chairman: 'openai/gpt-5.1' }, null).valid, true, 'no catalog: syntax only');
  });

  test('toggles respect the 8-member limit and clear the preset', () => {
    let d = { members: [], chairman: null, presetId: 'builtin:hybrid' };
    for (let i = 0; i < 10; i += 1) d = toggleMember(d, `x/m${i}`);
    assert.equal(d.members.length, MAX_MEMBERS);
    assert.equal(d.presetId, null);
    d = toggleMember(d, 'x/m0');
    assert.equal(d.members.includes('x/m0'), false);
    d = toggleChairman(d, 'x/m1');
    assert.equal(d.chairman, 'x/m1');
    assert.equal(toggleChairman(d, 'x/m1').chairman, null);
  });

  test('withPresetMatch re-detects a preset regardless of member order', () => {
    const d = withPresetMatch({ members: [...hybrid.members].reverse(), chairman: hybrid.chairman, presetId: null }, [hybrid]);
    assert.equal(d.presetId, 'builtin:hybrid');
    assert.equal(withPresetMatch({ ...d, chairman: 'spark/qwen3.8:27b' }, [hybrid]).presetId, null);
  });

  test('slugifyPresetName produces valid unique ids', () => {
    assert.equal(slugifyPresetName('My Local Mix!'), 'my-local-mix');
    assert.equal(slugifyPresetName('Çok güzel'), 'cok-guzel');
    assert.equal(slugifyPresetName('???'), 'preset');
    assert.equal(slugifyPresetName('mix', ['mix', 'mix-2']), 'mix-3');
    assert.match(slugifyPresetName('x'.repeat(80), ['x'.repeat(40)]), /^[a-z0-9-]{1,40}$/);
  });
});

describe('status, rankings and turns', () => {
  test('Mese pill: connected, locked, unreachable (Mese down, no token, backend down), checking', () => {
    assert.equal(meseStatus({ status: 'ready', data: { ok: true, locked: false } }).label, 'connected');
    assert.equal(meseStatus({ status: 'ready', data: { ok: true, locked: true } }).tone, 'warn');
    const down = meseStatus({ status: 'ready', data: { ok: false, token_set: true, error: 'ECONNREFUSED' } });
    assert.deepEqual([down.tone, down.label, down.detail], ['error', 'unreachable', 'ECONNREFUSED']);
    assert.equal(meseStatus({ status: 'ready', data: { ok: false, token_set: false } }).detail, 'MESE_API_KEY is not set in .env');
    const noToken = meseStatus({ status: 'ready', data: { ok: false, token_set: false, error: "MESE_API_KEY is not set in llm-council's .env" } });
    assert.equal(noToken.detail, "MESE_API_KEY is not set in llm-council's .env");
    assert.equal(meseStatus({ status: 'error', data: null, error: new Error('Cannot reach') }).tone, 'error');
    assert.equal(meseStatus({ status: 'loading' }).label, 'checking');
  });

  test('plan warnings are deduplicated and the key ignores member order', () => {
    const warnings = planWarnings({
      feasible: true,
      warnings: [
        { code: 'waves', host_id: 'spark', message: 'spark: runs in 2 waves, +~52 s' },
        { code: 'waves', host_id: 'spark', message: 'spark: runs in 2 waves, +~52 s' },
      ],
    });
    assert.deepEqual(warnings, [{ code: 'waves', message: 'spark: runs in 2 waves, +~52 s' }]);
    assert.equal(planWarnings({ feasible: false, warnings: [] })[0].code, 'infeasible');
    const plan = {
      feasible: true,
      warnings: [
        { code: 'unavailable', model: 'xai/grok-4.7', message: 'Grok 4.7 is unavailable (circuit_open)' },
        { code: 'cannot_rank', model: 'ft/legal-qwen-v3', message: 'ft/legal-qwen-v3 is Stage 1 only' },
      ],
    };
    assert.deepEqual(planWarnings(plan, ['xai/grok-4.7']).map((w) => w.code), ['cannot_rank'], 'picker already flags unavailable models');
    assert.equal(planWarnings(plan).length, 2);
    assert.equal(planKey({ members: ['b/x', 'a/y'], chairman: 'a/y' }), planKey({ members: ['a/y', 'b/x'], chairman: 'a/y' }));
    assert.deepEqual(JSON.parse(planKey({ members: ['b/x'], chairman: 'b/x' })), { members: ['b/x'], chairman: 'b/x' });
  });

  test('de-anonymisation uses display names and never clobbers longer labels', () => {
    const map = { 'Response A': 'spark/qwen3.8:27b', 'Response AB': 'anthropic/claude-opus-5-5' };
    const nameOf = (id) => describeModel(id, { index }).displayName;
    assert.equal(deAnonymizeText('Response A beats Response AB.', map, nameOf), '**Qwen3.8 27B** beats **Claude Opus 5.5**.');
    assert.equal(deAnonymizeText('Response A', null, nameOf), 'Response A');
    assert.equal(rankingSummary(['Response AB', 'Response A'], map, nameOf), 'Claude Opus 5.5 › Qwen3.8 27B');
    assert.equal(rankingSummary(['Response C', 'Response B'], null, nameOf), 'C › B');
  });

  test('appendTurn never duplicates a user message persisted at run start', () => {
    const assistant = { role: 'assistant', stage1: [] };
    const persisted = [{ role: 'user', content: 'q' }];
    assert.equal(endsWithUserMessage(persisted, 'q'), true);
    assert.deepEqual(appendTurn(persisted, 'q', assistant), [{ role: 'user', content: 'q' }, assistant]);
    assert.deepEqual(appendTurn([], 'q', assistant), [{ role: 'user', content: 'q' }, assistant]);
  });
});

describe('review fixes', () => {
  const stored = { members: ['a/x', 'b/y'], chairman: 'a/x', preset_id: 'builtin:hybrid', updated_at: 'now' };

  test('LEAD-1: opening and closing the picker never saves; a real change does', () => {
    const shown = { members: ['b/y', 'a/x'], chairman: 'a/x', presetId: 'builtin:hybrid' };
    assert.equal(sameSelection(shown, stored), true, 'member order does not matter');
    assert.equal(sameSelection({ ...shown, presetId: null }, stored), false, 'the preset is part of the selection');
    assert.equal(sameSelection(shown, null), false);
    assert.equal(sameSelection(null, undefined), true);
    assert.equal(normalizeDraft({ members: ['a/b'], chairman: '', preset_id: 'builtin:hybrid' }).presetId, 'builtin:hybrid');
    // Legacy conversation without a council: the picker shows the default draft; Done must not persist it.
    assert.equal(pickerShouldCommit({ edited: false, openedWith: shown, draft: shown, valid: true }), false);
    // The default changed underneath (presets reloaded) while the picker was open: still no write.
    const reloaded = { ...shown, chairman: 'b/y' };
    assert.equal(pickerShouldCommit({ edited: false, openedWith: shown, draft: reloaded, valid: true }), false);
    // Toggled a member off and on again: nothing changed.
    assert.equal(pickerShouldCommit({ edited: true, openedWith: shown, draft: { ...shown, members: ['a/x', 'b/y'] }, valid: true }), false);
    assert.equal(pickerShouldCommit({ edited: true, openedWith: shown, draft: { ...shown, members: ['a/x'] }, valid: true }), true);
    assert.equal(pickerShouldCommit({ edited: true, openedWith: shown, draft: { ...shown, presetId: 'my-mix' }, valid: true }), true);
    assert.equal(pickerShouldCommit({ edited: true, openedWith: shown, draft: { ...shown, members: [] }, valid: false }), false);
  });

  test('COUNCIL-5: catalog and presets reload when Mese health changes, and on focus at most every 10 s', () => {
    const healthy = { ok: true, providers_configured: 1, nodes_up: 1, nodes_total: 1, breakers_open: 0, locked: false, version: '0.1.0' };
    const before = healthSignature(healthy);
    const after = healthSignature({ ...healthy, providers_configured: 2 }); // a key was added in Mese admin
    assert.notEqual(before, after);
    assert.equal(healthSignature({ ok: false }), null);
    assert.equal(healthSignature({ ...healthy, latency_ms: 99 }), before, 'latency is not part of the signature');
    const base = { prevOk: true, prevSignature: before, ok: true, signature: before };
    assert.equal(resourceRefresh(base), null);
    assert.deepEqual(resourceRefresh({ ...base, signature: after }), { force: true });
    assert.deepEqual(resourceRefresh({ ...base, prevOk: false, prevSignature: null }), { force: true }, 'Mese came back');
    assert.equal(resourceRefresh({ ...base, prevOk: null, prevSignature: null }), null, 'first tick: the mount already loaded');
    assert.equal(resourceRefresh({ ...base, focus: true, sinceLastMs: FOCUS_RESOURCE_REFRESH_MS - 1 }), null);
    assert.deepEqual(resourceRefresh({ ...base, focus: true, sinceLastMs: FOCUS_RESOURCE_REFRESH_MS }), { force: false });
    assert.equal(resourceRefresh({ ...base, ok: false, signature: null, focus: true, sinceLastMs: 1e9 }), null);
  });

  test('COUNCIL-7/8 and COUNCIL-4: empty answers, chairman cost, unfinished turns', () => {
    assert.equal(humanizeError('empty_response', 'The model returned no answer text (finish_reason=length: …)').title, 'No answer text');
    assert.equal(humanizeError('run_failed', 'The council run failed: OSError: disk full').title, 'The council run failed');
    assert.equal(humanizeError('cancelled', 'The run was cancelled before it finished').title, 'Cancelled');
    assert.equal(chairmanCostUsd({ cost_usd: 0.5 }, { by_stage: { 3: 0.7 } }), 0.5);
    assert.equal(chairmanCostUsd({ cost_usd: 0 }, null), 0);
    assert.equal(chairmanCostUsd({ model: 'a/b' }, { by_stage: { 3: 0.7 } }), 0.7, 'turns stored before stage3.cost_usd');
    assert.equal(chairmanCostUsd({ model: 'a/b' }, { by_stage: { 3: 0 } }), null, '0 there may mean unknown');
    assert.equal(chairmanCostUsd({ model: 'a/b' }, undefined), null);
    assert.deepEqual([turnStatusBadge('complete'), turnStatusBadge(undefined)], [null, null]);
    assert.deepEqual(['partial', 'cancelled', 'error'].map((s) => turnStatusBadge(s).text), ['partial', 'cancelled', 'failed']);
  });
});

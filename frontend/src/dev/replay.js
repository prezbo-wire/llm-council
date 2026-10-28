/**
 * Dev-only fixture replay (`?replay=<name>`, §I.9). Runs the real App against an in-memory
 * backend that serves a fixture catalog, presets and conversations, and replays the WP0 event
 * fixtures in src/dev/fixtures/*.json through the normal SSE callback, so every card state can be
 * checked without a backend or Mese.
 *
 * Query parameters:
 *   replay=<fixture>      fixture to replay on Send (default hybrid-success)
 *   speed=<ms>            base delay between events (default 300)
 *   mese=ok|down|locked   Mese state for the status pill and the catalog (default ok)
 * At runtime: `window.__councilReplay.setMese('down')`, then "Refresh" in the Mese popover.
 */
import { ApiError } from '../api.js';
import { buildAssistantMessage, reduceCouncilEvents } from '../councilEvents.js';
import { LEGACY_CONVERSATIONS, REPLAY_ADMIN_URL, REPLAY_CATALOG, replayHealth } from './replayData.js';

const modules = import.meta.glob('./fixtures/*.json', { eager: true, import: 'default' });
export const REPLAY_FIXTURES = Object.fromEntries(Object.values(modules).map((fixture) => [fixture.name, fixture]));

const QUESTION = 'Should I use SQLite or PostgreSQL for a single-user app?';
const clone = (value) => JSON.parse(JSON.stringify(value));
const byId = new Map(REPLAY_CATALOG.map((m) => [m.id, m]));

/* ── Built-in preset selectors (dev mirror of §I.5) ── */
function allLocalPick(catalog) {
  const local = catalog.filter((m) => m.available && (m.kind === 'local' || m.kind === 'custom'));
  const members = local
    .filter((m) => m.can_rank)
    .sort((a, b) => (b.load_state === 'loaded') - (a.load_state === 'loaded') || (b.size_bytes ?? 0) - (a.size_bytes ?? 0))
    .slice(0, 4)
    .map((m) => m.id);
  const chair = local
    .filter((m) => m.can_chair)
    .sort((a, b) => (b.context_length ?? 0) - (a.context_length ?? 0) || (b.size_bytes ?? 0) - (a.size_bytes ?? 0))[0];
  return { members, chairman: chair ? chair.id : null };
}

function frontierPick(catalog) {
  const frontier = catalog.filter((m) => m.available && m.kind === 'cloud' && m.tags.includes('frontier'));
  const chair = catalog.find((m) => m.available && m.tags.includes('default_chair')) || frontier.find((m) => m.can_chair);
  return { frontier, chairman: chair ? chair.id : null };
}

function builtinPresets(catalog) {
  const local = allLocalPick(catalog);
  const { frontier, chairman } = frontierPick(catalog);
  const price = (m) => (m.price_per_mtok ? m.price_per_mtok.input + m.price_per_mtok.output : Infinity);
  const cheapest = [...frontier].sort((a, b) => price(a) - price(b)).slice(0, 2).map((m) => m.id);
  const preset = (id, name, description, members, chair) => ({
    id, name, description, members, chairman: chair, builtin: true, unavailable: [], created_at: null, updated_at: null,
  });
  return [
    preset('builtin:all-local', 'All local', 'Up to 4 local models, loaded first', local.members, local.chairman),
    preset('builtin:frontier-cloud', 'Frontier cloud', 'Frontier cloud models', frontier.slice(0, 5).map((m) => m.id), chairman),
    preset('builtin:hybrid', 'Hybrid', '2 cheapest frontier cloud + 2 local', [...cheapest, ...local.members.slice(0, 2)], chairman),
  ];
}

/* ── Plan (dev approximation of /v1/mese/plan warnings) ── */
function replayPlan({ members, chairman }) {
  const ids = [...new Set([...members, chairman])];
  const warnings = [];
  for (const id of ids) {
    const m = byId.get(id);
    if (!m) continue;
    if (!m.available) warnings.push({ code: 'unavailable', model: id, message: `${m.name} is unavailable (${m.unavailable_reason})` });
    if (!m.can_rank && members.includes(id)) warnings.push({ code: 'cannot_rank', model: id, message: `${id} is Stage 1 only (can_rank=false)` });
  }
  const cold = ids.map((id) => byId.get(id)).filter((m) => m && m.available && m.node && m.load_state !== 'loaded');
  const hosts = new Set(cold.map((m) => m.node.host_id));
  for (const host of hosts) {
    const onHost = cold.filter((m) => m.node.host_id === host);
    if (onHost.length > 1) warnings.push({ code: 'waves', host_id: host, message: `${host}: runs in 2 waves, +~${onHost.length * 20} s` });
  }
  return { feasible: true, items: [], hosts: [], warnings };
}

function eventDelay(event, base) {
  if (event.type === 'member_status' && event.state === 'loading') return base * 6;
  if (event.type === 'member_status' && event.state === 'queued') return base * 2;
  if (event.type === 'member_streaming' || event.type === 'final_streaming') return base * 0.7;
  return base;
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal && signal.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const timer = setTimeout(resolve, ms);
    if (signal) signal.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); }, { once: true });
  });
}

/** @returns {{api: object, initialConversationId: string}} props for <App /> */
export function createReplaySetup(params) {
  const names = Object.keys(REPLAY_FIXTURES).sort();
  const requested = params.get('replay');
  const fixture = REPLAY_FIXTURES[requested] || REPLAY_FIXTURES['hybrid-success'] || REPLAY_FIXTURES[names[0]];
  if (!REPLAY_FIXTURES[requested]) console.warn(`[replay] unknown fixture "${requested}"; available: ${names.join(', ')}`);
  const baseDelay = Math.max(0, Number(params.get('speed')) || 300);
  const state = { mese: params.get('mese') || 'ok', userPresets: [] };

  const now = () => new Date().toISOString();
  const conversations = new Map();
  const replayId = `replay-${fixture.name}`;
  conversations.set(replayId, {
    id: replayId, created_at: now(), title: `Replay: ${fixture.name}`,
    council: { ...fixture.council, preset_id: null, updated_at: now() }, messages: [],
  });
  for (const name of names) {
    const fx = REPLAY_FIXTURES[name];
    conversations.set(`persisted-${name}`, {
      id: `persisted-${name}`, created_at: now(), title: `Persisted: ${name}`,
      council: { ...fx.council, preset_id: null, updated_at: now() },
      messages: [{ role: 'user', content: QUESTION }, buildAssistantMessage(reduceCouncilEvents(fx.events))],
    });
  }
  for (const legacy of LEGACY_CONVERSATIONS) conversations.set(legacy.id, clone(legacy));

  const meseDown = () => state.mese === 'down';
  const unreachable = () => new ApiError('Mese is unreachable', { status: 503, code: 'mese_unreachable', detail: { code: 'mese_unreachable' } });
  const getConv = (id) => {
    const conv = conversations.get(id);
    if (!conv) throw new ApiError('Conversation not found', { status: 404 });
    return conv;
  };
  const checkCouncil = ({ members, chairman }) => {
    const bad = [...members, chairman].filter((id) => !byId.has(id));
    if (bad.length) throw new ApiError('unknown models', { status: 422, code: 'unknown_models', detail: { code: 'unknown_models', bad_ids: bad } });
    if (!byId.get(chairman).can_chair) throw new ApiError('chairman cannot chair', { status: 422, code: 'chairman_cannot_chair' });
  };
  const presetList = () => [
    ...builtinPresets(REPLAY_CATALOG),
    ...state.userPresets.map((p) => ({
      ...p, description: null, builtin: false,
      unavailable: [...p.members, p.chairman].filter((id) => byId.get(id) && !byId.get(id).available),
    })),
  ];

  const api = {
    async listConversations() {
      return [...conversations.values()].map((c) => ({ id: c.id, created_at: c.created_at, title: c.title, message_count: c.messages.length }));
    },
    async createConversation() {
      const conv = { id: `replay-new-${Date.now().toString(36)}`, created_at: now(), title: 'New Conversation', council: null, messages: [] };
      conversations.set(conv.id, conv);
      return clone(conv);
    },
    async getConversation(id) {
      return clone(getConv(id));
    },
    async sendMessage() {
      throw new ApiError('Not supported in replay', { status: 501 });
    },
    async putConversationCouncil(id, { members, chairman, preset_id = null }) {
      if (meseDown()) throw unreachable();
      checkCouncil({ members, chairman });
      const council = { members, chairman, preset_id, updated_at: now() };
      getConv(id).council = council;
      return { council: clone(council) };
    },
    async listModels() {
      await sleep(150);
      if (meseDown()) throw unreachable();
      return { mese: { ok: true, admin_url: REPLAY_ADMIN_URL }, models: clone(REPLAY_CATALOG) };
    },
    async getMeseHealth() {
      return replayHealth(state.mese);
    },
    async planCouncil(council, { signal } = {}) {
      await sleep(120, signal);
      if (meseDown()) throw unreachable();
      return replayPlan(council);
    },
    async listPresets() {
      return { presets: presetList() };
    },
    async savePreset(id, { name, members, chairman }) {
      if (id.startsWith('builtin:')) throw new ApiError('Built-in presets are read-only', { status: 409 });
      const existing = state.userPresets.find((p) => p.id === id);
      const preset = { id, name, members, chairman, created_at: existing ? existing.created_at : now(), updated_at: now() };
      state.userPresets = [...state.userPresets.filter((p) => p.id !== id), preset];
      return presetList().find((p) => p.id === id);
    },
    async deletePreset(id) {
      if (id.startsWith('builtin:')) throw new ApiError('Built-in presets are read-only', { status: 409 });
      state.userPresets = state.userPresets.filter((p) => p.id !== id);
      return null;
    },
    async sendMessageStreamV2(id, content, council, onEvent, { signal } = {}) {
      const conv = getConv(id);
      if (meseDown()) throw unreachable();
      if (council) checkCouncil(council);
      const first = conv.messages.length === 0;
      conv.messages.push({ role: 'user', content });
      let title = null;
      for (const event of fixture.events) {
        await sleep(eventDelay(event, baseDelay), signal);
        if (event.type === 'title_complete') title = event.data && event.data.title;
        onEvent(clone(event));
      }
      conv.messages.push(buildAssistantMessage(reduceCouncilEvents(fixture.events)));
      if (council) conv.council = { ...council, preset_id: null, updated_at: now() };
      if (first && title) conv.title = title;
    },
  };

  if (typeof window !== 'undefined') {
    window.__councilReplay = {
      fixtures: names,
      setMese(mode) {
        state.mese = mode;
        return `Mese is now "${mode}"; use Refresh in the Mese popover.`;
      },
    };
  }
  console.info(`[replay] fixture "${fixture.name}" (${fixture.description}); fixtures: ${names.join(', ')}`);
  return { api, initialConversationId: replayId };
}

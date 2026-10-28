/**
 * Dev-only data for `?replay=<fixture>` (never shipped: only imported from dev/replay.js).
 *
 * REPLAY_CATALOG is the CouncilModel projection (§I.5) of WP0's tests/fixtures/mese/v1_models.json,
 * so the fixture model ids resolve to the same names, kinds and availability as in the backend tests.
 * The legacy conversations are synthetic examples of the pre-Mese shapes.
 */

const SPARK = { id: 'spark', name: 'DGX Spark · Ollama', runtime: 'ollama', host_id: 'spark' };
const SPARK_VLLM = { id: 'spark-vllm', name: 'DGX Spark · vLLM', runtime: 'vllm', host_id: 'spark' };
const MINI1 = { id: 'mini1', name: 'Mac mini 1 · Ollama', runtime: 'ollama', host_id: 'mini1' };

function cloud(id, name, provider, price, { verified = false, tags = [], available = true, reason = null, context = null } = {}) {
  return {
    id, name, kind: 'cloud', provider, node: null, available, unavailable_reason: reason, load_state: null,
    can_rank: true, can_chair: true, context_length: context, size_bytes: null,
    price_per_mtok: { input: price[0], output: price[1] }, price_verified: verified, tags, lineage: null,
  };
}

function local(id, name, node, { load = 'loaded', size = null, context = 32768, tags = [], available = true, reason = null } = {}) {
  return {
    id, name, kind: 'local', provider: null, node, available, unavailable_reason: reason, load_state: load,
    can_rank: true, can_chair: true, context_length: context, size_bytes: size,
    price_per_mtok: null, price_verified: null, tags, lineage: null,
  };
}

const ANTHROPIC = { id: 'anthropic', name: 'Anthropic' };

export const REPLAY_CATALOG = [
  cloud('anthropic/claude-opus-5-5', 'Claude Opus 5.5', ANTHROPIC, [4, 20], { verified: true, tags: ['frontier', 'default_chair'], context: 1000000 }),
  cloud('anthropic/claude-sonnet-5-5', 'Claude Sonnet 5.5', ANTHROPIC, [2, 10], { verified: true, tags: ['frontier'], context: 1000000 }),
  cloud('anthropic/claude-haiku-5-5', 'Claude Haiku 5.5', ANTHROPIC, [0.1, 0.5], { verified: true, tags: ['fast'], context: 1000000 }),
  cloud('openai/gpt-6.1-sol', 'GPT-6.1 Sol', { id: 'openai', name: 'OpenAI' }, [2, 10], { tags: ['frontier'] }),
  cloud('google/gemini-4-argon', 'Gemini 4 Argon', { id: 'google', name: 'Google Gemini' }, [2, 10], {
    tags: ['frontier'], available: false, reason: 'no_credential',
  }),
  cloud('xai/grok-4.7', 'Grok 4.7', { id: 'xai', name: 'xAI' }, [2, 6], { tags: ['frontier'], available: false, reason: 'circuit_open' }),
  cloud('minimax/minimax-m3', 'MiniMax M3', { id: 'minimax', name: 'MiniMax' }, [0.3, 1.2], { available: false, reason: 'provider_disabled' }),
  local('spark/qwen3.8:27b', 'Qwen3.8 27B', SPARK, { size: 18253611008, context: 65536 }),
  local('spark/gpt-oss:120b', 'gpt-oss 120B', SPARK, { load: 'unloaded', size: 69793218560, context: 131072, tags: ['local_chair'] }),
  local('mini1/glimmer:30b', 'Muse Glimmer 30B', MINI1, { load: 'unknown', size: 23622320128, available: false, reason: 'node_down' }),
  local('spark-vllm/Qwen/Qwen3.8-27B', 'Qwen3.8 27B (vLLM)', SPARK_VLLM),
  {
    id: 'ft/legal-qwen-v3', name: 'Legal Qwen v3', kind: 'custom', provider: null, node: SPARK, available: true,
    unavailable_reason: null, load_state: 'unloaded', can_rank: false, can_chair: false, context_length: 32768,
    size_bytes: 17179869184, price_per_mtok: null, price_verified: null, tags: ['fine-tune'],
    lineage: { name: 'legal-qwen', version: 'v3' },
  },
];

export const REPLAY_ADMIN_URL = 'http://127.0.0.1:8791/admin/';

/** Pre-Mese conversations: old ids (rendered with the Legacy badge) and the old error shape. */
export const LEGACY_CONVERSATIONS = [
  {
    id: 'legacy-v2',
    created_at: '2026-02-03T09:12:13.141516',
    title: 'Legacy: WAL explained',
    messages: [
      { role: 'user', content: 'Explain write-ahead logging.' },
      {
        role: 'assistant',
        stage1: [
          { model: 'openai/gpt-5.1', response: 'WAL appends changes to a log before applying them.', latency_ms: 5400 },
          { model: 'google/gemini-3-pro-preview', response: 'Writers append to the WAL; readers keep reading the main file.', latency_ms: 7300 },
          { model: 'anthropic/claude-sonnet-4.5', response: 'The log is replayed after a crash so committed data survives.', latency_ms: 6100 },
        ],
        stage2: [
          {
            model: 'openai/gpt-5.1',
            ranking: 'Response A is thorough. Response B is concise. Response C covers durability.\n\nFINAL RANKING:\n1. Response C\n2. Response B\n3. Response A',
            parsed_ranking: ['Response C', 'Response B', 'Response A'],
          },
        ],
        stage3: { model: 'google/gemini-3-pro-preview', response: 'WAL records changes first and replays them after a crash.' },
      },
    ],
  },
  {
    id: 'legacy-all-failed',
    created_at: '2026-02-04T18:00:00.000000',
    title: 'Legacy: all models failed',
    messages: [
      { role: 'user', content: 'Summarize the attached report.' },
      {
        role: 'assistant',
        stage1: [],
        stage2: [],
        stage3: { model: 'error', response: 'All models failed to respond. Please try again.' },
      },
    ],
  },
];

/** Mese health payloads for `?mese=ok|down|locked`. */
export function replayHealth(mode) {
  const base = {
    ok: true, url: 'http://127.0.0.1:8790', admin_url: REPLAY_ADMIN_URL, token_set: true, latency_ms: 4,
    version: '1.0.0', locked: false, nodes_up: 2, nodes_total: 3, providers_configured: 3, breakers_open: 1, error: null,
  };
  if (mode === 'down') {
    return { ...base, ok: false, latency_ms: null, version: null, nodes_up: null, nodes_total: null, providers_configured: null, breakers_open: null, error: 'connect ECONNREFUSED 127.0.0.1:8790' };
  }
  if (mode === 'locked') return { ...base, locked: true };
  return base;
}

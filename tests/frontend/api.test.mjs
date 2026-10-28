// Tests for frontend/src/sse.js and frontend/src/api.js (§I.5 REST, §I.6 SSE) with a fake fetch.
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createSseParser } from '../../frontend/src/sse.js';
import { ApiError, createApi } from '../../frontend/src/api.js';
import { reduceCouncilEvents } from '../../frontend/src/councilEvents.js';

const here = dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(join(here, '..', '..', 'frontend', 'src', 'dev', 'fixtures', 'hybrid-success.json'), 'utf8'));

function collect(chunks, { end = true } = {}) {
  const events = [];
  const invalid = [];
  const parser = createSseParser((e) => events.push(e), (raw) => invalid.push(raw));
  chunks.forEach((c) => parser.push(c));
  if (end) parser.end();
  return { events, invalid };
}

describe('createSseParser', () => {
  test('parses data events separated by blank lines and ignores comments', () => {
    const { events } = collect([': keepalive\n\ndata: {"type":"a"}\n\n: ping\ndata: {"type":"b","x":1}\n\n']);
    assert.deepEqual(events, [{ type: 'a' }, { type: 'b', x: 1 }]);
  });

  test('handles events split across arbitrary chunk boundaries', () => {
    const text = fixture.events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
    for (const size of [1, 3, 7, 64, 1000]) {
      const chunks = [];
      for (let i = 0; i < text.length; i += size) chunks.push(text.slice(i, i + size));
      assert.deepEqual(collect(chunks).events, fixture.events, `chunk size ${size}`);
    }
  });

  test('accepts CRLF and lone CR line endings, including a CRLF split across chunks', () => {
    assert.deepEqual(collect(['data: {"type":"a"}\r', '\n\r\ndata: {"type":"b"}\r\r']).events, [{ type: 'a' }, { type: 'b' }]);
  });

  test('joins multi-line data and strips one leading space only', () => {
    assert.deepEqual(collect(['data:{"type":\ndata:  "a"}\n\n']).events, [{ type: 'a' }]);
  });

  test('tolerates consecutive single-line events without a blank separator', () => {
    assert.deepEqual(collect(['data: {"type":"a"}\ndata: {"type":"b"}\n\n']).events, [{ type: 'a' }, { type: 'b' }]);
  });

  test('flushes a final event without a trailing blank line on end()', () => {
    assert.deepEqual(collect(['data: {"type":"complete"}']).events, [{ type: 'complete' }]);
    assert.deepEqual(collect(['data: {"type":"complete"}'], { end: false }).events, []);
  });

  test('reports malformed JSON and typeless payloads without stopping', () => {
    const { events, invalid } = collect(['data: {oops\n\ndata: [1]\n\ndata: [DONE]\n\nevent: x\ndata: {"type":"ok"}\n\n']);
    assert.deepEqual(events, [{ type: 'ok' }]);
    assert.deepEqual(invalid, ['{oops', '[1]']);
  });
});

/** Fake fetch that records requests and answers from a route table. */
function fakeFetch(routes) {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : undefined, headers: init.headers });
    const key = `${init.method || 'GET'} ${url.replace('http://test', '')}`;
    const handler = routes[key];
    if (!handler) return new Response(JSON.stringify({ detail: 'Not Found' }), { status: 404 });
    return handler(init);
  };
  return { calls, fetchImpl };
}

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function sseResponse(chunks) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      chunks.forEach((c) => controller.enqueue(encoder.encode(c)));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

describe('createApi', () => {
  const council = { members: ['anthropic/claude-opus-5-5', 'spark/qwen3.8:27b'], chairman: 'anthropic/claude-opus-5-5' };

  test('§I.5 endpoints use the right method, path and body', async () => {
    const { calls, fetchImpl } = fakeFetch({
      'GET /api/models': () => json({ mese: { ok: true, admin_url: 'http://127.0.0.1:8791/admin/' }, models: [] }),
      'GET /api/mese/health': () => json({ ok: true }),
      'POST /api/council/plan': () => json({ feasible: true, items: [], hosts: [], warnings: [] }),
      'GET /api/presets': () => json({ presets: [] }),
      'PUT /api/presets/my-mix': () => json({ id: 'my-mix' }),
      'DELETE /api/presets/my-mix': () => new Response(null, { status: 204 }),
      'PUT /api/conversations/c%2F1/council': () => json({ council: { ...council, preset_id: null, updated_at: 'now' } }),
      'POST /api/conversations': () => json({ id: 'c1', council: null, messages: [] }),
    });
    const api = createApi({ baseUrl: 'http://test', fetchImpl });
    assert.deepEqual((await api.listModels()).models, []);
    assert.equal((await api.getMeseHealth()).ok, true);
    assert.equal((await api.planCouncil(council)).feasible, true);
    assert.deepEqual(await api.listPresets(), { presets: [] });
    await api.savePreset('my-mix', { name: 'My mix', ...council });
    assert.equal(await api.deletePreset('my-mix'), null);
    await api.putConversationCouncil('c/1', { ...council, preset_id: 'builtin:hybrid' });
    await api.createConversation({ council });

    assert.deepEqual(calls.map((c) => `${c.method} ${c.url.replace('http://test', '')}`), [
      'GET /api/models',
      'GET /api/mese/health',
      'POST /api/council/plan',
      'GET /api/presets',
      'PUT /api/presets/my-mix',
      'DELETE /api/presets/my-mix',
      'PUT /api/conversations/c%2F1/council',
      'POST /api/conversations',
    ]);
    assert.deepEqual(calls[2].body, council);
    assert.deepEqual(calls[4].body, { name: 'My mix', ...council });
    assert.deepEqual(calls[6].body, { ...council, preset_id: 'builtin:hybrid' });
    assert.deepEqual(calls[7].body, { council });
  });

  test('listModels({refresh: true}) asks the backend to skip its catalog cache', async () => {
    const { calls, fetchImpl } = fakeFetch({
      'GET /api/models': () => json({ mese: { ok: true }, models: [] }),
      'GET /api/models?refresh=1': () => json({ mese: { ok: true }, models: [] }),
    });
    const api = createApi({ baseUrl: 'http://test', fetchImpl });
    await api.listModels({ refresh: true });
    await api.listModels({ refresh: false });
    assert.deepEqual(calls.map((c) => c.url.replace('http://test', '')), ['/api/models?refresh=1', '/api/models']);
  });

  test('error bodies become ApiError with the backend code (422 unknown_models, 503, validation)', async () => {
    const { fetchImpl } = fakeFetch({
      'POST /api/council/plan': () => json({ detail: { code: 'unknown_models', bad_ids: ['x/y'] } }, 422),
      'GET /api/models': () => json({ detail: { code: 'mese_unreachable', message: 'connect refused' } }, 503),
      'PUT /api/presets/builtin%3Ahybrid': () => json({ detail: 'built-in presets are read-only' }, 409),
      'GET /api/presets': () => json({ detail: [{ msg: 'field required' }, { msg: 'bad id' }] }, 422),
    });
    const api = createApi({ baseUrl: 'http://test', fetchImpl });
    await assert.rejects(api.planCouncil(council), (e) => e instanceof ApiError && e.status === 422 && e.code === 'unknown_models' && e.detail.bad_ids[0] === 'x/y');
    await assert.rejects(api.listModels(), (e) => e.code === 'mese_unreachable' && e.message === 'connect refused' && e.status === 503);
    await assert.rejects(api.savePreset('builtin:hybrid', { name: 'x', ...council }), (e) => e.status === 409 && e.message === 'built-in presets are read-only');
    await assert.rejects(api.listPresets(), (e) => e.code === 'invalid_request' && e.message === 'field required; bad id');
  });

  test('a network failure becomes backend_unreachable; an abort stays an AbortError', async () => {
    const down = createApi({ baseUrl: 'http://test', fetchImpl: async () => { throw new TypeError('fetch failed'); } });
    await assert.rejects(down.listConversations(), (e) => e instanceof ApiError && e.code === 'backend_unreachable');
    const aborted = createApi({
      baseUrl: 'http://test',
      fetchImpl: async () => { throw new DOMException('Aborted', 'AbortError'); },
    });
    await assert.rejects(aborted.planCouncil(council), (e) => e.name === 'AbortError');
  });

  test('sendMessageStreamV2 posts content + council to /message/stream/v2 and streams every event', async () => {
    const wire = fixture.events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
    const { calls, fetchImpl } = fakeFetch({
      'POST /api/conversations/abc/message/stream/v2': () => sseResponse([': keepalive\n\n', wire.slice(0, 501), wire.slice(501)]),
    });
    const api = createApi({ baseUrl: 'http://test', fetchImpl });
    const received = [];
    await api.sendMessageStreamV2('abc', 'Which DB?', { ...council, presetId: 'builtin:hybrid' }, (e) => received.push(e));
    assert.deepEqual(calls[0].body, { content: 'Which DB?', council });
    assert.deepEqual(received, fixture.events);
    assert.equal(reduceCouncilEvents(received).phase, 'complete');
  });

  test('sendMessageStreamV2 omits the council when none is given', async () => {
    const { calls, fetchImpl } = fakeFetch({
      'POST /api/conversations/abc/message/stream/v2': () => sseResponse(['data: {"type":"complete"}\n\n']),
    });
    await createApi({ baseUrl: 'http://test', fetchImpl }).sendMessageStreamV2('abc', 'q', null, () => {});
    assert.deepEqual(calls[0].body, { content: 'q' });
  });

  test('sendMessageStreamV2 rejects pre-stream errors before any event', async () => {
    const { fetchImpl } = fakeFetch({
      'POST /api/conversations/abc/message/stream/v2': () => json({ detail: { code: 'chairman_cannot_chair' } }, 422),
    });
    const received = [];
    await assert.rejects(
      createApi({ baseUrl: 'http://test', fetchImpl }).sendMessageStreamV2('abc', 'q', council, (e) => received.push(e)),
      (e) => e.code === 'chairman_cannot_chair' && e.status === 422,
    );
    assert.deepEqual(received, []);
  });

  test('the removed v1 sendMessageStream is gone', () => {
    const api = createApi({ baseUrl: 'http://test', fetchImpl: async () => json({}) });
    assert.equal(api.sendMessageStream, undefined);
    for (const name of ['listModels', 'getMeseHealth', 'planCouncil', 'listPresets', 'savePreset', 'deletePreset', 'putConversationCouncil', 'sendMessageStreamV2']) {
      assert.equal(typeof api[name], 'function', name);
    }
  });
});

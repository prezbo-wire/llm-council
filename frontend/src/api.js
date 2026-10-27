/**
 * API client for the LLM Council backend (Mese ARCHITECTURE §I.5 REST, §I.6 SSE).
 *
 * `createApi()` takes the base URL and a fetch implementation so the client can be exercised
 * under `node --test`; the app uses the default `api` instance.
 */
import { createSseParser } from './sse.js';

const DEFAULT_API_PORT = 8001;

function resolveApiBase() {
  const envBase = import.meta.env?.VITE_API_BASE;
  if (envBase) return envBase;
  if (typeof window === 'undefined') return `http://localhost:${DEFAULT_API_PORT}`;
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:${DEFAULT_API_PORT}`;
}

/** Error thrown for non-2xx responses; `code` comes from `{detail: {code}}` when the backend sends one. */
export class ApiError extends Error {
  constructor(message, { status = 0, code = null, detail = null } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

async function readErrorBody(response) {
  try {
    const text = await response.text();
    return text ? JSON.parse(text) : null;
  } catch {
    return null;
  }
}

/** Normalises FastAPI error bodies: `{detail: {code, message}}`, `{detail: "…"}` or `{detail: [validation…]}`. */
export async function toApiError(response, fallbackMessage) {
  const body = await readErrorBody(response);
  const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : body;
  let code = null;
  let message = fallbackMessage;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    code = typeof detail.code === 'string' ? detail.code : null;
    if (typeof detail.message === 'string' && detail.message) message = detail.message;
  } else if (typeof detail === 'string' && detail) {
    message = detail;
  } else if (Array.isArray(detail) && detail.length > 0) {
    code = 'invalid_request';
    message = detail.map((d) => d && d.msg).filter(Boolean).join('; ') || fallbackMessage;
  }
  return new ApiError(message, { status: response.status, code, detail });
}

function networkError(error) {
  if (error && error.name === 'AbortError') return error;
  return new ApiError('Cannot reach the LLM Council backend', { status: 0, code: 'backend_unreachable', detail: String(error) });
}

/**
 * @param {{baseUrl?: string, fetchImpl?: typeof fetch}} [options]
 */
export function createApi({ baseUrl = resolveApiBase(), fetchImpl } = {}) {
  const doFetch = fetchImpl || ((...args) => globalThis.fetch(...args));
  const url = (path) => `${baseUrl}${path}`;
  const enc = encodeURIComponent;

  async function request(path, { method = 'GET', body, signal, fallback = 'Request failed', expectJson = true } = {}) {
    const init = { method, signal };
    if (body !== undefined) {
      init.headers = { 'Content-Type': 'application/json' };
      init.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await doFetch(url(path), init);
    } catch (error) {
      throw networkError(error);
    }
    if (!response.ok) throw await toApiError(response, fallback);
    if (!expectJson || response.status === 204) return null;
    return response.json();
  }

  return {
    /* ── Conversations ── */
    listConversations() {
      return request('/api/conversations', { fallback: 'Failed to list conversations' });
    },
    /** @param {{council?: {members: string[], chairman: string}, preset_id?: string}} [options] */
    createConversation(options = {}) {
      const body = {};
      if (options.council) body.council = options.council;
      if (options.preset_id) body.preset_id = options.preset_id;
      return request('/api/conversations', { method: 'POST', body, fallback: 'Failed to create conversation' });
    },
    getConversation(conversationId) {
      return request(`/api/conversations/${enc(conversationId)}`, { fallback: 'Failed to get conversation' });
    },
    /** Non-streaming send; returns `{stage1, stage2, stage3, metadata, failures, usage}`. */
    sendMessage(conversationId, content, council = null) {
      const body = council ? { content, council } : { content };
      return request(`/api/conversations/${enc(conversationId)}/message`, {
        method: 'POST',
        body,
        fallback: 'Failed to send message',
      });
    },
    /** PUT the council used for the conversation's next message. */
    putConversationCouncil(conversationId, { members, chairman, preset_id = null }) {
      return request(`/api/conversations/${enc(conversationId)}/council`, {
        method: 'PUT',
        body: { members, chairman, preset_id },
        fallback: 'Failed to save the council',
      });
    },

    /* ── Mese catalog, health and planning ── */
    /**
     * `{mese: {ok, admin_url}, models: CouncilModel[]}`; 503 `mese_unreachable` when Mese is down.
     * `refresh` skips the backend's 10 s catalog cache.
     */
    listModels({ signal, refresh = false } = {}) {
      return request(refresh ? '/api/models?refresh=1' : '/api/models', { signal, fallback: 'Failed to load models' });
    },
    getMeseHealth({ signal } = {}) {
      return request('/api/mese/health', { signal, fallback: 'Failed to check Mese' });
    },
    /** Proxied V1PlanResponse for a draft council. */
    planCouncil({ members, chairman }, { signal } = {}) {
      return request('/api/council/plan', {
        method: 'POST',
        body: { members, chairman },
        signal,
        fallback: 'Failed to plan the council',
      });
    },

    /* ── Presets ── */
    listPresets() {
      return request('/api/presets', { fallback: 'Failed to load presets' });
    },
    savePreset(presetId, { name, members, chairman }) {
      return request(`/api/presets/${enc(presetId)}`, {
        method: 'PUT',
        body: { name, members, chairman },
        fallback: 'Failed to save the preset',
      });
    },
    deletePreset(presetId) {
      return request(`/api/presets/${enc(presetId)}`, {
        method: 'DELETE',
        expectJson: false,
        fallback: 'Failed to delete the preset',
      });
    },

    /**
     * Sends a message and streams §I.6 events. Pre-stream failures (422 unknown_models,
     * 503 mese_unreachable, …) reject with ApiError before any event is delivered.
     * Resolves when the stream closes.
     * @param {string} conversationId
     * @param {string} content
     * @param {{members: string[], chairman: string} | null} council
     * @param {(event: {type: string}) => void} onEvent
     * @param {{signal?: AbortSignal}} [options]
     */
    async sendMessageStreamV2(conversationId, content, council, onEvent, { signal } = {}) {
      const body = council ? { content, council: { members: council.members, chairman: council.chairman } } : { content };
      let response;
      try {
        response = await doFetch(url(`/api/conversations/${enc(conversationId)}/message/stream/v2`), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
          body: JSON.stringify(body),
          signal,
        });
      } catch (error) {
        throw networkError(error);
      }
      if (!response.ok) throw await toApiError(response, 'Failed to send message');
      if (!response.body) throw new ApiError('The backend returned an empty stream', { status: response.status });

      const parser = createSseParser(onEvent, (raw, error) => {
        console.error('Failed to parse SSE event:', error, raw.slice(0, 200));
      });
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parser.push(decoder.decode(value, { stream: true }));
      }
      parser.push(decoder.decode());
      parser.end();
    },
  };
}

export const api = createApi();

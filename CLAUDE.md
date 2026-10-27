# CLAUDE.md - Technical Notes for LLM Council

This file contains technical details, architectural decisions, and important implementation notes for future development sessions.

## Project Overview

LLM Council is a 3-stage deliberation system where multiple LLMs collaboratively answer user questions. The key innovation is anonymized peer review in Stage 2, preventing models from playing favorites.

Every model call goes through **Mese**, the local model router in `/Users/prezbo/Development/mese`. llm-council holds no provider key: it sends `Authorization: Bearer $MESE_API_KEY` to Mese's OpenAI-compatible `/v1` API, and Mese routes to cloud providers or local nodes (Ollama, llama.cpp, vLLM, LM Studio). The integration contract is Mese `docs/ARCHITECTURE.md` §I (summary in `docs/mese-integration.md`).

## Architecture

### Backend Structure (`backend/`)

**`config.py`**
- `Settings` (frozen dataclass) built by `load_settings()` from env / `.env`: `MESE_URL`, `MESE_API_KEY`, `MESE_ADMIN_URL`, `TITLE_MODEL`, `DEFAULT_COUNCIL`/`DEFAULT_CHAIRMAN`, `COUNCIL_DATA_DIR`, `PRESETS_PATH`, `COUNCIL_HOST`, `COUNCIL_PORT`, `COUNCIL_ALLOWED_ORIGINS`
- `LEGACY_DISPLAY_NAMES`: read-only names for pre-Mese model ids in old conversations
- `mask_secret()` (`…last4` at most) and `safe_url()`; the Mese token is never logged or returned
- `Settings.allowed_hosts`: the `Host` allowlist that matches CORS (hostnames of `COUNCIL_ALLOWED_ORIGINS` + `localhost`/`127.0.0.1`/`::1` + `COUNCIL_HOST` when it is a specific address; `None` = any host when the origins include `*`); `host_of()` parses `Host` headers (`[::1]:8001` → `::1`)
- Backend runs on **port 8001** (NOT 8000 - user had another app on 8000)

**`mese_client.py`** - `MeseClient`, one pooled `httpx.AsyncClient` (opened/closed in the FastAPI lifespan, `Timeout(connect=5, read=60, write=30, pool=10)`, `trust_env=False`)
- `stream_chat()`: POST `/v1/chat/completions` with `stream: true` and `mese: {client: "llm-council", run_id, conversation_id, stage, events: true}`; no `max_tokens`/`temperature` (Mese policy applies)
- SSE parser: `:` comments/keepalives ignored; `event: mese` frames → `on_status` (`queued`/`loading`/`started`/`retrying`); first `reasoning_content` → `on_status({"state": "thinking"})` (reasoning text is dropped); `content` → `on_delta`; the finish chunk carries `finish_reason`, `usage`, the `mese` summary and `error`; `[DONE]`
- **Always returns a `MemberResult`, never `None`**, never raises for upstream errors: `ok` only for `stop`/`length` **with answer text** (no text, e.g. a reasoning model that spent its budget reasoning → `empty_response`); `content_filter` → `content_filtered`; in-stream error → Mese `error.code`; pre-stream HTTP error → code from the body; connect failure → `mese_unreachable`; no finish chunk → `upstream_error`
- `list_models()` (10 s cache), `plan()`, `warmup()` (best-effort), `status()`; `list_models`/`plan` raise `MeseError`

**`council/`** - The Core Logic (replaces the old 529-line `council.py`)
- `prompts.py`: ranking, chairman and title prompts (verbatim from the original), plus label helpers
- `ranking.py`: `parse_ranking_from_text()` and `calculate_aggregate_rankings()` (unchanged)
- `runner.py`: `run_council(query, council, conversation_id, emit, *, deps, is_first_message)` → `TurnResult`, the single runner for streaming and non-streaming turns (§I.4). `is_first_message` means "generate a title" (the routes pass it while the title is still the default)
- `events.py`: builders for every §I.6 SSE event (camelCase), `to_sse()`
- `title.py`: title model choice and cleanup
- `tasks.py`: `TaskTracker` (spawn, cancel and await every task; Python 3.10, no `TaskGroup`)

**`catalog.py`**: `CatalogIndex` over Mese's `/v1/models` (alias resolution), `project_model()` → `CouncilModel`, `normalize_council()` (422 `unknown_models` / `chairman_cannot_chair`), `MemberInfo` for runs and snapshots

**`presets.py`**: built-in selectors `builtin:all-local`, `builtin:frontier-cloud`, `builtin:hybrid` (resolved against the live catalog on every request) and `PresetStore` for user presets in `data/presets.json`

**`storage.py`**
- JSON conversations in `data/conversations/<id>.json`; ids are validated (no path traversal)
- **Atomic writes**: temp file in the same directory → `fsync` → `os.replace`
- Every read uses `.get()` with defaults, so legacy files load unchanged
- Conversation: `{id, created_at, title, council?, messages[]}`; `council` = `{members, chairman, preset_id, updated_at}` (used for the *next* message)
- Assistant message: `{role, stage1, stage2, stage3, metadata, council_snapshot, failures, usage, status}`. `stage1`/`stage2` keep their legacy shapes; `metadata` (label_to_model, aggregate_rankings) is now persisted; `stage3` = `{model, response, latency_ms?, cost_usd, error}`
- `status`: `complete` / `partial` for a finished turn, `cancelled` (client gone) / `error` (unexpected exception) for a turn that stopped early, so a user message never stays without its assistant message
- `update_turn(conversation_id, run_id, patch, title=)`: patches the assistant message of one run (by `council_snapshot.run_id`) and sets the title in one atomic write

**`app_state.py`**: `AppContext` (settings, Mese client, stores), HTTP error helpers, and `council_for_send()` (request council → conversation council → `DEFAULT_COUNCIL` → `builtin:hybrid` → 422 `council_required`; the chosen council is persisted)

**`api_council.py`**: `/api/models` (`?refresh=1` skips the 10 s catalog cache), `/api/mese/health`, `/api/council/plan`, `/api/presets*`, `PUT /api/conversations/{id}/council`

**`main.py`**
- `create_app(settings, mese_client=None)` app factory + lifespan; module-level `app`; `python -m backend.main [--host] [--port]`
- CORS: exactly `COUNCIL_ALLOWED_ORIGINS`, `allow_credentials=False`, no origin regex
- `HostAllowlistMiddleware` (outermost): 400 `invalid_host` unless the `Host` header is in `Settings.allowed_hosts`, so a DNS-rebinding page (same-origin, CORS moot) cannot reach the API. The UI calls `<page hostname>:8001`, so every hostname the UI is opened by must be a CORS origin (the start scripts add the LAN and tailnet IPs)
- Conversation routes; `POST …/message` (JSON) and `POST …/message/stream/v2` (SSE) accept an optional `council`, validated and saved before the run starts. The v1 `…/message/stream` route is gone (404)
- The SSE generator drains one `asyncio.Queue` fed by the runner's `emit` (plus a 15 s `: keepalive`); its `finally` cancels and awaits every task, which closes every in-flight Mese stream when the client disconnects

### Frontend Structure (`frontend/src/`)

See Mese ARCHITECTURE §I.9 for the full list. Highlights:
- `api.js`: REST + `sendMessageStreamV2(id, content, council, onEvent)`; the v1 stream call is removed
- `councilEvents.js`: pure reducer for every §I.6 event (unknown events ignored), tested with `node --test tests/frontend/*.test.mjs`
- Council picker (`components/CouncilPicker/`): closing it saves the council (`PUT …/council`) only when the user changed the selection while it was open and it differs from the stored one (`pickerShouldCommit`, `sameSelection` in `council/draft.js`); `hooks/useMeseResources.js` reloads the catalog and presets when Mese's health changes (e.g. a key added in Mese admin) and at most every 10 s on window focus
- `ModelBadge` (Cloud / Local / FT / Legacy), member cards with queued/loading/retrying/thinking states, Stage-2 per-ranker rows, final-answer error state, and a read-only Mese status pill in the sidebar
- `dev/replay.js` + `?replay=<fixture>` replays `src/dev/fixtures/*.json` without a backend
- Light mode theme; primary color #4a90e2; all markdown wrapped in `.markdown-content` (see `index.css`)

## Key Design Decisions

### Stage 2 Prompt Format
The Stage 2 prompt is very specific to ensure parseable output:
```
1. Evaluate each response individually first
2. Provide "FINAL RANKING:" header
3. Numbered list format: "1. Response C", "2. Response A", etc.
4. No additional text after ranking section
```

This strict format allows reliable parsing while still getting thoughtful evaluations.

### Council Run Semantics (Mese §I.4)
- `run_id = "run_" + uuid4().hex`; every Mese call carries `run_id`, `conversation_id` and `stage` (`stage1`/`stage2`/`stage3`/`title`), so Mese's usage ledger groups a whole turn
- Warmup: the chairman only, and only when it is local (Mese warmup never evicts)
- Stage 1: all members in parallel; results are kept in member order (labels A…H follow that order)
- Stage 2: rankers are Stage-1 successes with `can_rank` (fine-tunes default to false). Skipped with `stage2_skipped` when there are fewer than 2 responses or no rankers. A ranker whose context is too small (`ceil(len(prompt)/4) > context_length - 1024`) gets `stage2_member_skipped {reason: "context"}`
- Stage 3: on failure `stage3 = {model, response: "", cost_usd, error: {code, message}}` plus `final_error`; never fake text. No Stage-1 success → `final_error {code: "no_responses"}`. `stage3.cost_usd` is the chairman's cost (also on failure)
- The finished turn is persisted right after Stage 3, **before** the title, so a reload or disconnect while the title is generated never loses a paid turn
- Title runs after that, while the conversation still has the default title and no title call has finished (`title_attempted`; so a first turn cancelled before its title is named on the next turn, but a failed title is not retried on every turn): `TITLE_MODEL` only if it is local or the council already has a cloud model, else the chairman. An all-local council never sends the question to the cloud for a title. Its cost and any title failure are written onto the stored turn together with the title (`update_turn`, one atomic write)
- A run that stops early still persists its assistant message: `status: "cancelled"` (client disconnected) or `"error"` (unexpected exception), with the Stage-1/Stage-2 results that finished, `stage3.error` `{code: "cancelled" | "run_failed"}`, and a `cancelled` failure for every Mese call still in flight
- `failures[]` collects `{stage, model, code, message}`; `status` is `partial` when there is any failure; `usage` sums cost and tokens per stage from Mese's figures

### De-anonymization Strategy
- Models receive: "Response A", "Response B", etc.
- Backend creates mapping: `{"Response A": "anthropic/claude-opus-5-5", ...}`
- Frontend displays model names in **bold** for readability
- Users see explanation that original evaluation used anonymous labels
- This prevents bias while maintaining transparency

### Error Handling Philosophy
- Continue with successful responses if some models fail (graceful degradation)
- Never fail the entire request due to single model failure
- Every failure is explicit: a `member_error` / `stage2_member_error` / `final_error` event with a Mese error code, and a persisted `failures[]` entry
- Unavailable models (no key, node down) may be picked; they fail fast at run time instead of being dropped silently

### UI/UX Transparency
- All raw outputs are inspectable via tabs
- Parsed rankings shown below raw text for validation
- Users can verify system's interpretation of model outputs
- This builds trust and allows debugging of edge cases

## Important Implementation Details

### Relative Imports
All backend modules use relative imports (e.g., `from .config import ...`) not absolute imports. This is critical for Python's module system to work correctly when running as `python -m backend.main`.

### Python Version
`.python-version` is 3.10. Keep all backend code 3.10-compatible (no `asyncio.TaskGroup`, no 3.11+ syntax).

### Port Configuration
- Backend: 8001 (changed from 8000 to avoid conflict)
- Frontend: 5173 (Vite default)
- Mese: 8790 (inference, used by the backend), 8791 (admin panel, linked from the UI)
- Update `COUNCIL_PORT` and `frontend/src/api.js` together if changing the backend port

### Markdown Rendering
All markdown components must be wrapped in `<div className="markdown-content">` for proper spacing. This class is defined globally in `index.css`.

### Model Configuration
There is no hardcoded model list anymore. Councils are chosen per conversation in the UI (or via presets) from Mese's catalog; `DEFAULT_COUNCIL`/`DEFAULT_CHAIRMAN` and `TITLE_MODEL` are optional env overrides. Provider keys, nodes and model loading are managed in the Mese admin panel.

## Common Gotchas

1. **Module Import Errors**: Always run backend as `python -m backend.main` from project root, not from backend directory
2. **CORS / Host Issues**: The browser origin must be in `COUNCIL_ALLOWED_ORIGINS`; the start scripts add LAN and tailnet origins on :5173. The backend also rejects any `Host` outside that list (400 `invalid_host`, logged as a warning), so opening the UI by a name that is not a CORS origin (e.g. a MagicDNS name) fails until its origin is added. Tests use `http://127.0.0.1:8001` as the base URL for the same reason
3. **Mese not running / no token**: `/api/models` returns 503 `mese_unreachable`; the sidebar pill shows the state. Mese runs on the DGX Spark: `MESE_URL` must be `http://<spark tailnet IP>:8790`, and `MESE_API_KEY` is the contents of `~/.mese/tokens/llm-council.token` **on the Spark** (copy it over ssh without printing it; README "Configure llm-council")
4. **Ranking Parse Failures**: If models don't follow format, fallback regex extracts any "Response X" patterns in order
5. **Metadata**: `metadata` is now persisted with each assistant message (it used to be ephemeral)

## Testing Notes

`uv run pytest tests/` runs everything against a mocked Mese: `tests/conftest.py` (WP0, read-only) provides `MeseMock`, an `httpx.MockTransport` that replays the golden Mese transcripts in `tests/fixtures/mese/` and records requests and stream closes, plus a socket guard that blocks non-loopback hosts. WP7 suites: `test_mese_client.py`, `test_council.py`, `test_council_persistence.py` (persist-before-title, cancelled/error turns), `test_api.py` (incl. the Host allowlist), `test_storage.py`, `test_presets.py`, `test_config.py` (shared helpers in `test_support.py`). Live acceptance runs Mese's `bun run smoke` with `LLM_COUNCIL_DIR` set.

## Data Flow Summary

```
User Query (+ optional council)
    ↓
Validate council against the Mese catalog (422 / 503 before streaming) → persist it
    ↓
council_started → member_queued × N (+ chairman warmup if local)
    ↓
Stage 1: parallel Mese streams → member_status / member_streaming → member_complete | member_error
    ↓
Stage 2: anonymize → parallel rankers (can_rank, context pre-check) → stage2_complete {data, metadata}
    ↓
Stage 3: chairman stream → final_ready | final_error → stage3_complete
    ↓
Persist assistant message atomically (before the title; a cancelled/crashed run persists status cancelled/error)
    ↓
Title (while the title is the default) → update the stored turn + title in one write → title_complete
    ↓
run_summary {costUsd, tokens, failures, status} → complete
```

The entire flow is async/parallel where possible to minimize latency.

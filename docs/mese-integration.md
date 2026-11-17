# Mese integration

llm-council is a client of **Mese**, a local OpenAI-compatible router (repo `/Users/prezbo/Development/mese`).
The full design is in Mese's `docs/ARCHITECTURE.md`, **section I** ("llm-council integration"):

- §I.1 Mese endpoints llm-council calls (`/v1/models`, `/v1/chat/completions`, `/v1/mese/plan|warmup|status`)
- §I.2 consuming Mese streams (`backend/mese_client.py`) and the stream-outcome → `MemberResult` table
- §I.3 the Python contract (`backend/contracts.py`)
- §I.4 council run semantics (`backend/council/runner.py`)
- §I.5–§I.7 the REST API, SSE events and conversation JSON (copied below)
- §I.8–§I.10 file changes, backwards compatibility and cutover

Contract files owned by WP0 (read-only for other packages): `backend/contracts.py`, `backend/schemas.py`,
`tests/conftest.py`, `tests/fixtures/**` and `frontend/src/dev/fixtures/**`. Golden Mese transcripts in
`tests/fixtures/mese/` are byte-identical copies of Mese's `tests/fixtures/sse/mese/`; `expected.json` there
lists the normative `MemberResult` for each one.

## Where Mese runs

**Mese runs on the DGX Spark**, the only inference host (Mese ARCHITECTURE §A, runbook in Mese's
`docs/deploy.md` §2). llm-council is deployed next to it with `deploy/spark/deploy.sh` (systemd `--user`
units `llm-council` and `llm-council-ui` on the Spark's tailnet IP, `:8001` and `:5173`); its
`~/.config/llm-council/mese-token.env` is rewritten from Mese's token file on every install. The Mac is the
browser and the development machine. A development run of llm-council on the Mac uses `.env`:

| Setting (llm-council `.env`, development on the Mac) | Value |
|---|---|
| `MESE_URL` | `http://<spark tailnet IPv4>:8790`. Mese binds `/v1` to the Spark's tailnet IP; the tailnet ACL admits a development Mac on 8790 only through its optional `dev-mac` rule. |
| `MESE_API_KEY` | the contents of `~/.mese/tokens/llm-council.token` **on the Spark**, copied with `ssh` straight into `.env` so it is never printed (README "Configure llm-council") |
| `MESE_ADMIN_URL` | the default `http://127.0.0.1:8791/admin/`; it works while `ssh -N -L 8791:127.0.0.1:8791 spark` is open, because the admin listener is loopback-only on the Spark by default |

The start scripts check `GET $MESE_URL/healthz` (no token needed) and, if it fails, say whether to set
`MESE_URL` or to check the ACL and `systemctl --user status mese` on the Spark. `GET /api/mese/health`
reports `ok`, `token_set` and `locked` without ever returning the token. A Mese started on the Mac for
development (Mese README "Run locally") works with the defaults unchanged; no real local model runs there.

The backend itself has no authentication and binds `0.0.0.0` by default, so it is a credentialed bridge to
the tailnet-only Mese for anyone who can reach port 8001. The README's "Exposure" section explains
`COUNCIL_HOST=<tailnet IP>`; whether to change the default is an open decision.

## Run semantics in brief (Mese ARCHITECTURE §I.2, §I.4)

- A `stop`/`length` finish with no answer text is a member failure (`empty_response`, not retryable), so an
  empty answer is never labelled, ranked or synthesized.
- The finished turn is persisted right after Stage 3, **before** the title call. The title runs while the
  conversation still has the default title and no title call has finished (`title_attempted`); its cost,
  any title failure and the title itself are then written onto the stored turn in one atomic write
  (`ConversationStore.update_turn`, which also sets `title_attempted`), so a failed title is not retried
  on every later turn.
- A run that stops early still persists its assistant message: `status: "cancelled"` (client gone) or
  `"error"` (unexpected exception), with the Stage-1/Stage-2 results that finished and
  `stage3.error.code` `cancelled` / `run_failed`.
- `stage3.cost_usd` carries the chairman's cost, also on failure.
- Requests whose `Host` header is not a CORS origin hostname, loopback or a specific `COUNCIL_HOST` get
  400 `invalid_host`.

## Residency (Mese ARCHITECTURE §G.6)

Mese gives every local model row a residency class: `on_demand` (the default), `resident` (preloaded, kept
loaded and restored after a displacement) or `exclusive` (loads only when needed, unloading the fewest
resident bytes it must; the residents reload afterwards). A model that cannot fit beside the resident set is
effectively exclusive ("exclusive (auto)"). The class is set per model in Mese admin; llm-council only reads it.

- **Catalog:** `CouncilModel.residency` copies only `class` (as `effective`), `configured`, `est_load_s`
  (`0` when loaded) and `observed_tok_s` from `/v1/models` `mese.residency`. It is null for cloud models,
  runtimes Mese does not load, and an older Mese; null counts as `on_demand`.
- **Presets:** `builtin:all-local` (and the local half of `builtin:hybrid`) is resident-first, and exclusive
  models join only to reach 2 members; the chair is the resident `can_chair` model with the largest context,
  else the v1 rule over every local `can_chair` model. Without residency data the selection is exactly v1's.
  There is no built-in with a big exclusive chair: pick it, or save it as your own preset.
- **Plan:** `POST /api/council/plan` sends `{models: members, chairman}` (a chair that is not a member is never
  listed in `models`, which Mese plans as Stage-1/2 members), so Mese also simulates the turn and
  returns `turn` (`loads`, `load_s`, `restore_s`, `stages`), which passes through unchanged (an older Mese
  that rejects `chairman` with 400 `invalid_request` is asked again with the chair in `models`, and has no turn line). Warnings
  `exclusive`, `slow` and `cpu_offload` render like every other warning, as `plan-<code>` lines.
- **Picker:** each local row shows a pill (Resident, Resident · reloading, On demand · ~N s, Exclusive · ~N s,
  Exclusive (auto) · ~N s), plus Slow · ~N tok/s below 10 tok/s and CPU offload when the plan warns about it.
  The turn line reads "Local: no model loads" or "Local: 1 model load (+~48 s); residents reload ~52 s later".
- **Runs:** unchanged. The chairman warmup stays best-effort; Mese answers `skip` for an exclusive model, which
  then loads when Stage 3 needs it.

## ChatGPT plan models (Mese `chatgpt/*`)

Mese can serve models billed to the user's own ChatGPT Plus/Pro plan ("Sign in with ChatGPT"; Mese ARCHITECTURE
§D.1–§D.3 and §F). They are ordinary cloud models to llm-council, with one rule: **billing never switches**.

- **Ids.** `chatgpt/<slug>`, e.g. `chatgpt/gpt-6.1-sol`, separate from the API-key ids (`openai/gpt-6.1-sol`).
  Mese rejects aliases between the two; llm-council also ignores any alias whose two ends differ in billing
  (`CatalogIndex`), so a stored council, a preset or Last used never resolves one to the other.
- **Catalog.** `CouncilModel.billing` copies `/v1/models` `mese.billing`: `api_key`, `chatgpt_plan` or `local`
  (null for an older Mese, where only the `chatgpt/` prefix marks a plan model). Plan models have pricing `0`/`0`
  (no USD; they count toward the plan's usage limits) and no residency. Unavailable reasons: `plan_limited` (Mese
  paused every `chatgpt/*` model after a usage limit: still listed, greyed out), `plan_unavailable` (not eligible,
  or the account no longer lists the model) and `no_credential` (ChatGPT not connected in Mese admin).
- **Presets.** The built-ins never pick a plan model, even one tagged `frontier` or `default_chair`: they are
  selectors resolved on every request, so including plan models would switch the same preset between plan usage
  and API-key billing whenever ChatGPT is connected, paused or resumed (and Hybrid's "cheapest" rule would always
  take the $0 plan model). A plan model joins a council only by a manual pick or a user preset.
- **Runs.** A usage limit is `plan_limit_exceeded`: HTTP 429 before any byte when Mese refuses at admission while
  paused (`tests/fixtures/mese/plan_limit_paused.json`), or a finish `error` in the stream, with no text when ChatGPT
  refused before answering (`plan_limit_pre_stream.sse`, `events: true` commits the stream first) or after text
  (`plan_limit_midstream.sse`); all three are captured from Mese. It is never retried, by Mese or llm-council, and no other model stands
  in. The member gets `member_error` (or `stage2_member_error`; a chairman `final_error` and `stage3.error`) with
  that code and a `failures[]` entry; partial text is never ranked or synthesized.
- **Cost.** Mese reports `cost_usd: 0` with `cost_source: 'plan'`, so plan calls add $0 to `usage`. The UI labels
  them "$0 (plan)" (a mixed run: "$0.0123 + $0 (plan)") from the model's billing; `council_started` members and
  chairman and `council_snapshot` entries carry `billing` for that (llm-council additions).
- **Plan check.** `/v1/mese/plan` reads plan models without admission or a token refresh (it never uses up Mese's
  single trial request after a pause): an available one is `ready` with no price warning, an unavailable one is
  `unavailable` with `reason` `no_credential`, `plan_limited` or `plan_unavailable` and a warning that says what to
  do in Mese admin.
- **Title.** An API-key `TITLE_MODEL` is used only when the council has an API-key cloud model; a council whose only
  cloud models are plan-billed is titled by its chairman, so choosing the plan never adds an API charge.
- **UI.** A "Plan" badge; the row price reads "ChatGPT plan"; the picker shows "Using ChatGPT plan · Manage usage"
  while a member or the chair is plan-billed; a member or chairman failing with `plan_limit_exceeded` reads
  "Usage limit reached / Review your plan or this app's limit in ChatGPT settings." with **Manage usage**
  (`https://chatgpt.com/settings/usage`, new tab) as the primary action. `?replay=plan-limit` shows the card
  without a backend.

The sections below are a copy of Mese ARCHITECTURE §I.5–§I.7 as of 2026-10-28. If they ever disagree,
the Mese document wins.

---

## I.5 llm-council REST API (for the frontend)

Bodies are snake_case. Pydantic models live in `backend/schemas.py` (WP0).

**`CouncilConfig` validation:**
- `members`: 1–8 unique ids matching `^[a-z0-9][a-z0-9-]*/\S{1,200}$` (or a `ft/…` id);
- `chairman`: required.

**Semantic checks** run against the Mese catalog at save or send time:
- An id not in the catalog → 422 `{detail: {code: "unknown_models", bad_ids: [...]}}`.
- A chairman without `can_chair` → 422 `{detail: {code: "chairman_cannot_chair"}}`.
- *Unavailable* ids are allowed; they fail fast at run time with an explicit `member_error`.
- Mese down → 503 `{detail: {code: "mese_unreachable", message}}`.

| Method & path | Request | Response |
|---|---|---|
| `GET /api/models` | `?refresh=1` (optional) bypasses the backend's 10 s catalog cache; the frontend sends it when Mese comes back or its health signature changes | `{"mese": {"ok": true, "admin_url": "http://127.0.0.1:8791/admin/"}, "models": [CouncilModel]}`; 503 if Mese is down |
| `GET /api/mese/health` | – | `{"ok", "url", "admin_url", "token_set", "latency_ms", "version", "locked", "nodes_up", "nodes_total", "providers_configured", "breakers_open", "error"}`. Never contains the token. |
| `POST /api/council/plan` | `CouncilConfig` | `V1PlanResponse` (proxied with `chairman`, so `turn` is set; see "Residency" above) |
| `GET /api/presets` | – | `{"presets": [Preset]}`: "Last used" (`builtin:last-used`) first, then the built-ins (resolved at request time), then user presets. |
| `PUT /api/presets/{id}` | `{"name", "members", "chairman"}` (`id` matches `^[a-z0-9-]{1,40}$`) | `Preset`; 409 for `builtin:*` |
| `DELETE /api/presets/{id}` | – | 204; 409 for `builtin:*` |
| `GET /api/conversations` | – | unchanged list metadata |
| `POST /api/conversations` | `{"council"?: CouncilConfig, "preset_id"?: str}` | `Conversation` (with `council`, possibly null) |
| `GET /api/conversations/{id}` | – | `Conversation` (adds `council`; messages may carry the new optional fields) |
| `PUT /api/conversations/{id}/council` | `CouncilConfig & {"preset_id"?: str}` | `{"council": ConversationCouncil}`. It applies to the *next* message. |
| `POST /api/conversations/{id}/message/stream/v2` | `{"content": str, "council"?: CouncilConfig}` | SSE (§I.6). A provided council is validated and saved as the conversation's council. |
| `POST /api/conversations/{id}/message` | `{"content", "council"?}` | `{stage1, stage2, stage3, metadata, failures, usage}` |
| ~~`POST …/message/stream`~~ (v1) | **removed**; the frontend uses only v2 | |

If neither the request nor the conversation has a council, the backend uses `DEFAULT_COUNCIL`/`DEFAULT_CHAIRMAN` from env, else the resolved `builtin:hybrid`, else 422 `council_required`.

**Host allowlist:** every request whose `Host` header is not allowed gets 400 `{detail: {code: "invalid_host"}}` (DNS-rebinding guard; it runs before CORS). Allowed hosts are the hostnames of `COUNCIL_ALLOWED_ORIGINS`, plus `localhost`, `127.0.0.1` and `::1`, plus `COUNCIL_HOST` when it is a specific address. A `*` origin disables the check; a missing `Host` is rejected. Opening the UI by a name that is not a CORS origin (for example a MagicDNS name) therefore fails until that origin is added.

```ts
// Shapes as seen by the frontend (JSON, snake_case)
interface CouncilModel {
  id: string; name: string; kind: 'cloud' | 'local' | 'custom';
  provider: { id: string; name: string } | null;
  node: { id: string; name: string; runtime: string; host_id: string } | null;
  available: boolean; unavailable_reason: string | null; load_state: string | null;
  residency: { effective: 'on_demand' | 'resident' | 'exclusive'; configured: 'on_demand' | 'resident' | 'exclusive';
               est_load_s: number | null; observed_tok_s: number | null } | null;   // llm-council addition (§G.6)
  can_rank: boolean; can_chair: boolean; context_length: number | null; size_bytes: number | null;
  price_per_mtok: { input: number; output: number } | null;   // USD per 1M; null for local/unknown
  price_verified: boolean | null;
  billing: 'api_key' | 'chatgpt_plan' | 'local' | null;    // llm-council addition: mese.billing (see "ChatGPT plan"); null from an older Mese
  tags: string[];
  lineage: { name: string; version: string } | null;
}
interface Preset {
  id: string;                 // 'builtin:last-used' | 'builtin:all-local' | 'builtin:frontier-cloud' | 'builtin:hybrid' | user slug
  name: string; description: string | null;
  members: string[]; chairman: string | null;   // null when a built-in can't resolve (e.g. no local models)
  builtin: boolean;
  unavailable: string[];      // members/chairman currently unavailable
  created_at: string | null; updated_at: string | null;   // Last used: updated_at = when it was sent
  source_preset_id?: string | null;   // Last used only: the preset its council came from
  conversation_id?: string | null;    // Last used only: the conversation it was sent in
}
interface ConversationCouncil { members: string[]; chairman: string; preset_id: string | null; updated_at: string }
```

**Presets** (`backend/presets.py`):
- User presets are stored in `data/presets.json` as `{"version": 1, "presets": [{id, name, members, chairman, created_at, updated_at}]}`, written atomically.
- **Built-ins are selectors**, not stored, so they stay valid as models come and go:

| Built-in | Members | Chairman |
|---|---|---|
| `builtin:all-local` | Up to 4 available `local`/`custom` models with `can_rank`: resident first, then loaded, then by `size_bytes` descending. Effective-exclusive models are appended (largest first) only to reach 2 members. | The resident local `can_chair` model with the largest `context_length` (ties: largest size); with none, the available local `can_chair` model with the largest `context_length`, ties broken by largest size. |
| `builtin:frontier-cloud` | Available API-key cloud models tagged `frontier`, at most 5 (never a ChatGPT-plan model). | The model tagged `default_chair`, else the first frontier model with `can_chair`; never a plan model. |
| `builtin:hybrid` | The 2 cheapest available API-key `frontier` cloud models, plus the 2 local models chosen by the all-local rule. | As frontier-cloud. |

**Last used** (`builtin:last-used`; an llm-council addition that Mese §I.5 does not describe yet):
- It is the validated, canonical council of the most recent message a send route accepted, in any conversation. Runs that were later cancelled or failed count; sends rejected with 422/503 and a `PUT …/council` without a send do not. A failing write only logs a warning.
- Stored in `<COUNCIL_DATA_DIR>/last_council.json` as `{"version": 1, "council": {members, chairman, preset_id, conversation_id, used_at}}`, written atomically. It replaces the frontend's former per-browser localStorage copy, so every UI (Mac, phone, Spark) sees the same value.
- It is resolved on every request: aliases become catalog ids; ids that left the catalog are dropped and listed in `unavailable` together with the kept ids that are unavailable now; `chairman` is null when it is gone or can no longer chair. Before any send it has no members.
- It is immutable (409 on PUT/DELETE) and is never stored as a conversation's `preset_id`: `POST /api/conversations {"preset_id": "builtin:last-used"}` and `PUT …/council` store its `source_preset_id` while the council still equals the record, else null.
- The picker starts a conversation without a council from it (when it has members and a chairman), else from `builtin:hybrid`.

## I.6 SSE events to the frontend (`/message/stream/v2`)

Every event is a `data: {"type": …}` line with camelCase fields. Existing events keep all their current fields, and new fields are additive. `memberId` is the model id, as today.

| Event | Status | Payload |
|---|---|---|
| `council_started` | new, first | `{runId, members: [{memberId, displayName, kind, providerId, nodeId, canRank}], chairman: {modelId, displayName, kind, providerId, nodeId}}`; llm-council adds `billing` to each member and the chairman |
| `member_queued` | +fields | `{memberId, displayName, kind, providerId, nodeId, canRank}` |
| `member_status` | new | `{memberId, state: 'queued'\|'loading'\|'started'\|'retrying'\|'thinking', position?, nodeId?, attempt?, reason?}` |
| `member_streaming` | unchanged | `{memberId, deltaText}` |
| `member_complete` | +fields | `{memberId, response, latencyMs, ttftMs, costUsd, usage: {promptTokens, completionTokens, reasoningTokens} \| null}` |
| `member_error` | +fields | `{memberId, message, code, retryable}`. Stage 1 only, so it never overwrites a "done" card. |
| `stage1_complete` | unchanged | `{data: Stage1Entry[]}` |
| `stage2_start` | unchanged | `{}` |
| `stage2_member_status` | new | `{memberId, state}` |
| `stage2_model_complete` | unchanged | `{memberId, ranking}` |
| `stage2_member_error` | new | `{memberId, code, message}` |
| `stage2_member_skipped` | new | `{memberId, reason: 'context'}` |
| `stage2_skipped` | new | `{reason: 'fewer_than_two_responses' \| 'no_rankers'}`. It is followed by `stage2_complete` with `data: []`. |
| `stage2_complete` | +fields | `{data, metadata: {label_to_model, aggregate_rankings, failures}}` |
| `final_generating` | unchanged | `{}` |
| `final_status` | new | `{state, position?, nodeId?}` |
| `final_streaming` | unchanged | `{deltaText}` |
| `final_ready` | +fields | `{content, latencyMs, costUsd, model}` |
| `final_error` | new | `{code, message}` |
| `stage3_complete` | unchanged | `{data: Stage3Entry}` (may include `error`) |
| `title_complete` | unchanged | `{data: {title}}` |
| `run_summary` | new, before `complete` | `{runId, costUsd, tokens: {prompt, completion}, failures: Failure[], status: 'complete'\|'partial'}` |
| `complete` | unchanged | `{}` |
| `error` | unchanged | `{message}` (unexpected exception only) |

**Mapping from Mese to frontend events:**

| Mese input | Frontend event |
|---|---|
| `queued` frame | `member_status queued` (with position) |
| `loading` frame | `loading` |
| `started` frame | `started` |
| `retrying` frame | `retrying` |
| first reasoning delta | `thinking` |
| content delta | `member_streaming` |
| finish chunk | `member_complete` or `member_error` |

## I.7 Conversation JSON additions (all optional; old files load unchanged)

```json
{
  "id": "…", "created_at": "…", "title": "…",
  "council": { "members": ["anthropic/claude-opus-5-5", "spark/qwen3.8:27b"], "chairman": "anthropic/claude-opus-5-5",
               "preset_id": "builtin:hybrid", "updated_at": "2026-10-27T12:00:00Z" },
  "messages": [
    { "role": "user", "content": "…" },
    { "role": "assistant",
      "stage1": [{ "model": "spark/qwen3.8:27b", "response": "…", "latency_ms": 5400, "cost_usd": 0 }],
      "stage2": [{ "model": "spark/qwen3.8:27b", "ranking": "…", "parsed_ranking": ["Response B", "Response A"] }],
      "stage3": { "model": "anthropic/claude-opus-5-5", "response": "…", "latency_ms": 21000,
                  "cost_usd": 0.0212, "error": null },
      "metadata": { "label_to_model": { "Response A": "spark/qwen3.8:27b" }, "aggregate_rankings": [ … ] },
      "council_snapshot": {
        "run_id": "run_9f…",
        "members": [{ "id": "spark/qwen3.8:27b", "display_name": "Qwen3.8 27B", "kind": "local", "provider_id": null,
                      "node_id": "spark", "upstream_model": "qwen3.8:27b", "can_rank": true, "lineage": null }],
        "chairman": { "id": "anthropic/claude-opus-5-5", "display_name": "Claude Opus 5.5", "kind": "cloud",
                      "provider_id": "anthropic", "node_id": null, "upstream_model": "claude-opus-5-5" } },
      "failures": [{ "stage": "1", "model": "xai/grok-4.7", "code": "credential_missing", "message": "No API key in Mese" }],
      "usage": { "cost_usd": 0.0412, "prompt_tokens": 18234, "completion_tokens": 4120,
                 "by_stage": { "1": 0.011, "2": 0.009, "3": 0.0212, "title": 0 } },
      "status": "complete"
    }
  ]
}
```

**Rules for these fields:**
- `stage1` and `stage2` keep their **exact legacy shapes**. Failures go into `failures`, so `Stage1.jsx` and `Stage2.jsx` render old and new messages alike.
- `metadata` is **now persisted**, which fixes the "Missing Metadata" gotcha in llm-council's `CLAUDE.md`.
- `council_snapshot.members[].lineage` is `{name, version, training_run_id}` for fine-tunes, so rankings can later be analysed per fine-tune version (brief §5).
- llm-council adds `billing` (`mese.billing`) to every `council_snapshot` member and the chairman, so a stored turn still labels its ChatGPT-plan calls "$0 (plan)" after the model leaves the catalog.
- `stage3.cost_usd` is the chairman's cost (also on a chairman failure); `null` when unknown or the turn did not finish.
- `status` is `complete`, `partial` (some failures), `cancelled` (the run was cancelled, §I.4 step 9) or `error` (the run crashed).
- Storage writes are atomic: write to a temp file in the same directory, `fsync`, then `os.replace`.

# LLM Council

![llmcouncil](header.jpg)

The idea of this repo is that instead of asking a question to your favorite LLM provider (e.g. OpenAI GPT 5.1, Google Gemini 3.0 Pro, Anthropic Claude Sonnet 4.5, xAI Grok 4, eg.c), you can group them into your "LLM Council". This repo is a simple, local web app that essentially looks like ChatGPT except it sends your query to multiple LLMs (cloud models and models running on your own machines), it then asks them to review and rank each other's work, and finally a Chairman LLM produces the final response. All model calls go through [Mese](../mese), a local model router: llm-council never holds a provider key.

In a bit more detail, here is what happens when you submit a query:

1. **Stage 1: First opinions**. The user query is given to all LLMs individually, and the responses are collected. The individual responses are shown in a "tab view", so that the user can inspect them all one by one.
2. **Stage 2: Review**. Each individual LLM is given the responses of the other LLMs. Under the hood, the LLM identities are anonymized so that the LLM can't play favorites when judging their outputs. The LLM is asked to rank them in accuracy and insight.
3. **Stage 3: Final response**. The designated Chairman of the LLM Council takes all of the model's responses and compiles them into a single final answer that is presented to the user.

## Vibe Code Alert

This project was 99% vibe coded as a fun Saturday hack because I wanted to explore and evaluate a number of LLMs side by side in the process of [reading books together with LLMs](https://x.com/karpathy/status/1990577951671509438). It's nice and useful to see multiple responses side by side, and also the cross-opinions of all LLMs on each other's outputs. I'm not going to support it in any way, it's provided here as is for other people's inspiration and I don't intend to improve it. Code is ephemeral now and libraries are over, ask your LLM to change it in whatever way you like.

## Setup

### 1. Install dependencies

The project uses [uv](https://docs.astral.sh/uv/) for Python and npm for the frontend.

```bash
uv sync
cd frontend && npm install && cd ..
```

### 2. Run Mese first

[Mese](../mese) is the model router (repo `/Users/prezbo/Development/mese`). It holds the provider API keys
and the local nodes (DGX Spark, Mac minis), loads local models on demand and records usage and cost.
llm-council only talks to Mese, over HTTP with a bearer token.

**Default: Mese on the DGX Spark.** Mese runs on the Spark as a systemd user service, next to Ollama, and
serves its API on the Spark's tailnet IP (port 8790). On the Spark, in the mese checkout:

```bash
deploy/spark/install-user.sh     # no sudo; bun, build, keyring file, ~/.mese tokens, the service; prints only paths
```

The full runbook (tailnet ACL, Ollama node, admin access) is Mese's `docs/deploy.md` §1–§2. The admin panel
listens on the Spark's `127.0.0.1:8791` only; from the Mac, open a tunnel and keep it open while you use it:

```bash
ssh -N -L 8791:127.0.0.1:8791 spark     # "spark" = the Spark's SSH host, e.g. spark-2380
open http://127.0.0.1:8791/admin/       # log in with the Spark's ~/.mese/admin-token
```

Keys, nodes, model loading and usage are managed there, not in llm-council.

**Alternative: Mese on this machine.** In the mese repo: `bun install --frozen-lockfile`, `bun run mese:init`,
`bun run build && bun src/server/main.ts` (inference on 127.0.0.1:8790, admin on 127.0.0.1:8791). Then
`MESE_URL` can stay at its default and the token is the local `~/.mese/tokens/llm-council.token`.

### 3. Configure llm-council

Settings live in `.env` in the project root (see `.env.example`). With Mese on the Spark:

```bash
MESE_URL=http://<spark tailnet IPv4>:8790   # the Spark's `tailscale ip -4`; its MagicDNS name works too
```

`MESE_API_KEY` is the Mese client token. It lives on the Mese host in `~/.mese/tokens/llm-council.token`.
Copy it from the Spark into `.env` without printing it (it never reaches the screen or the shell history):

```bash
token="$(ssh spark 'cat ~/.mese/tokens/llm-council.token')" && [ -n "$token" ] &&
  { grep -v '^MESE_API_KEY=' .env 2>/dev/null; printf 'MESE_API_KEY=%s\n' "$token"; } > .env.new &&
  chmod 600 .env.new && mv .env.new .env
unset token
```

(With Mese on this machine, use `cat ~/.mese/tokens/llm-council.token` instead of the `ssh` command.)
Check that the Mac reaches Mese: `curl -fsS http://<spark tailnet IPv4>:8790/healthz` prints `{"ok":true}`.
If it times out, check the tailnet ACL (the Mac must be allowed to reach the Spark on 8790; Mese's
`deploy/tailscale-acl.hujson`) and `systemctl --user status mese` on the Spark.

`MESE_API_KEY` is only read from the environment or `.env` and is never logged or sent to the browser.
Optional settings:

| Variable | Default | Purpose |
|---|---|---|
| `MESE_URL` | `http://127.0.0.1:8790` | Mese inference API. With Mese on the Spark: `http://<spark tailnet IPv4>:8790`. |
| `MESE_ADMIN_URL` | `http://127.0.0.1:8791/admin/` | Linked from the UI ("add it in Mese admin"). With Mese on the Spark it works while the `ssh -L 8791:127.0.0.1:8791` tunnel is open. |
| `TITLE_MODEL` | chairman | Model for conversation titles. Used only if it is local or the council already has a cloud model, so an all-local council never sends your question to the cloud for a title. |
| `DEFAULT_COUNCIL`, `DEFAULT_CHAIRMAN` | the `builtin:hybrid` preset | Council for conversations that have none (comma list of model ids, plus one chairman id) |
| `COUNCIL_HOST`, `COUNCIL_PORT` | `0.0.0.0`, `8001` | Backend bind address. The backend has no auth and can spend money: see "Exposure" below. |
| `COUNCIL_ALLOWED_ORIGINS` | localhost and 127.0.0.1 on 5173 and 3000 | CORS allowlist (comma list). The start scripts add your LAN and tailnet origins on :5173. Its hostnames are also the backend's `Host` allowlist (see below). |
| `COUNCIL_DATA_DIR`, `PRESETS_PATH` | `data`, `data/presets.json` | Conversation and user-preset storage |

#### Exposure: the backend bind

The backend has **no authentication**, and it listens on `0.0.0.0:8001` by default, so every interface of
this machine accepts it, the LAN included. Anyone who can reach that port can read every conversation and
run councils with llm-council's Mese token, spending money on your cloud keys. With Mese on the Spark this
matters more: Mese itself is reachable only from this Mac over the tailnet, but llm-council forwards any
request it receives to Mese with its own token. (Whether to change the default bind is an open decision; the
default is unchanged for now.)

Ways to narrow it:

- `COUNCIL_HOST=<this Mac's tailnet IPv4>` (`tailscale ip -4`): only tailnet devices allowed by your ACL reach
  the backend, and nothing on the LAN does. The frontend calls the backend on the hostname you opened it with,
  so then open the UI as `http://<tailnet IPv4>:5173`; `http://localhost:5173` can no longer reach the backend.
- `COUNCIL_HOST=127.0.0.1`: only a browser on this Mac (`http://localhost:5173`).
- In either case, set a daily cloud budget in Mese (admin → Settings) as a spending cap.

The backend also checks the `Host` header of every request (a DNS-rebinding guard): it must be a hostname
from `COUNCIL_ALLOWED_ORIGINS`, `localhost`/`127.0.0.1`/`::1`, or a specific `COUNCIL_HOST`; anything else
gets 400 `invalid_host`. Opening the UI by a name that is not a CORS origin (for example a MagicDNS name)
therefore fails until you add that origin.

### 4. Pick a council

Councils are chosen in the UI, per conversation, with the council picker above the message box: 1 to 8
members plus a chairman that is allowed to chair. Changes apply to the next question. Model ids are Mese
catalog ids such as `anthropic/claude-opus-5-5`, `spark/qwen3.8:27b` or `ft/legal-qwen-v3`.

One-click presets:

- **All local**: up to 4 local models (loaded ones first); the local model with the largest context chairs. Costs $0.
- **Frontier cloud**: the available frontier cloud models; the default chair leads.
- **Hybrid**: the two cheapest frontier cloud models plus two local models.

Built-in presets are resolved against the live Mese catalog each time, so they keep working as models come
and go. Your own presets are saved in `data/presets.json`. Models that are unavailable (no key, node down)
can still be picked; they fail fast with an explicit error card instead of being silently dropped.

### Migrating from the OpenRouter version

Existing conversations in `data/conversations/` load unchanged; old model ids are shown with a "Legacy"
badge and are never remapped to other models.

1. Install Mese: on the Spark, `deploy/spark/install-user.sh` in the mese checkout (or, locally, `bun run mese:init`).
2. In the Mese admin panel: add your provider keys and the Spark node.
3. Set `MESE_URL` (with Mese on the Spark) and copy the Mese host's `~/.mese/tokens/llm-council.token` into
   llm-council's `.env` as `MESE_API_KEY`, as shown in "Configure llm-council".
4. Start llm-council.
5. Remove `OPENROUTER_API_KEY` from `.env` and revoke it at OpenRouter.

## Running the Application

**Option 1: Use the start script**
```bash
./start.sh            # foreground; Ctrl+C stops both servers
./start-detached.sh   # background; stop with ./stop-detached.sh
```

The scripts check `GET $MESE_URL/healthz` and warn when `MESE_API_KEY` is missing or Mese is not reachable
(with a hint to set `MESE_URL` when Mese runs on the Spark). They also print the backend bind address, and
export `COUNCIL_ALLOWED_ORIGINS` with your LAN and tailnet origins added.

**Option 2: Run manually**

Terminal 1 (Backend):
```bash
uv run python -m backend.main            # or: --host <tailnet IPv4> --port 8001 (see "Exposure")
```

Terminal 2 (Frontend):
```bash
cd frontend
npm run dev
```

Then open http://localhost:5173 in your browser.

To access from another device on your LAN or tailnet, open `http://<HOST_IP>:5173`. The frontend calls the
backend at `http://<HOST_IP>:8001`, so that port must be reachable and the origin must be in
`COUNCIL_ALLOWED_ORIGINS` (the start scripts handle this; the same list is the backend's `Host` allowlist).

## Tests

```bash
uv run pytest tests/
```

The tests use a mocked Mese (`httpx.MockTransport` replaying recorded Mese streams); they never touch the
network, real keys or real models.

## Tech Stack

- **Backend:** FastAPI (Python 3.10+), async httpx client for Mese
- **Models:** [Mese](../mese) (cloud providers direct, plus local Ollama, llama.cpp, vLLM and LM Studio nodes)
- **Frontend:** React + Vite
- **Storage:** JSON files in `data/conversations/` (atomic writes) and `data/presets.json`
- **Package Management:** uv for Python, npm for JavaScript

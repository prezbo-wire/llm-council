"""REST + SSE API (§I.5, §I.6) through the ASGI app with a mock Mese."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest
from starlette.middleware.cors import CORSMiddleware

from backend.main import create_app
from conftest import CONVERSATION_FIXTURES, TEST_MESE_TOKEN, MeseMock, build_mese_sse
from test_support import (FT, GPT, GPT_OSS, GROK, OPUS, QWEN, SONNET, VLLM, chat_calls, make_client, make_settings,
                          parse_sse, route_member)

COUNCIL = {"members": [OPUS, GPT, QWEN], "chairman": OPUS}
#: A loopback Host: the API rejects any Host that the CORS policy does not name (DNS rebinding).
BASE_URL = "http://127.0.0.1:8001"


@pytest.fixture
def mock():
    mock = MeseMock()
    for model in (OPUS, GPT, QWEN, SONNET, VLLM, GPT_OSS, FT):
        route_member(mock, model)
    return mock


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
async def api(mock, settings):
    mese = make_client(mock)
    app = create_app(settings, mese_client=mese)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client:
        client.app = app  # type: ignore[attr-defined]
        yield client
    await mese.aclose()


async def _new_conversation(api, **body):
    response = await api.post("/api/conversations", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# ── council validation ───────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/message", "/message/stream/v2"])
async def test_unknown_member_is_422_unknown_models(api, path):
    conv = await _new_conversation(api)
    body = {"content": "hi", "council": {"members": [OPUS, "nope/model-x"], "chairman": "nope/chair"}}
    response = await api.post(f"/api/conversations/{conv['id']}{path}", json=body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "unknown_models" and detail["bad_ids"] == ["nope/model-x", "nope/chair"]
    assert (await api.get(f"/api/conversations/{conv['id']}")).json()["messages"] == []


async def test_non_chair_chairman_is_422(api):
    conv = await _new_conversation(api)
    response = await api.post(f"/api/conversations/{conv['id']}/message/stream/v2",
                              json={"content": "hi", "council": {"members": [OPUS, QWEN], "chairman": FT}})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "chairman_cannot_chair"


async def test_syntax_errors_are_422(api):
    conv = await _new_conversation(api)
    bad = {"content": "hi", "council": {"members": [OPUS, OPUS], "chairman": OPUS}}
    assert (await api.post(f"/api/conversations/{conv['id']}/message", json=bad)).status_code == 422
    too_many = {"content": "hi", "council": {"members": [f"p/m{i}" for i in range(9)], "chairman": OPUS}}
    assert (await api.post(f"/api/conversations/{conv['id']}/message", json=too_many)).status_code == 422


async def test_mese_down_is_503(api, mock):
    conv = await _new_conversation(api)
    mock.unreachable = True
    for method, path, body in [("POST", f"/api/conversations/{conv['id']}/message/stream/v2", {"content": "hi", "council": COUNCIL}),
                               ("POST", f"/api/conversations/{conv['id']}/message", {"content": "hi", "council": COUNCIL}),
                               ("GET", "/api/models", None), ("GET", "/api/presets", None),
                               ("POST", "/api/council/plan", COUNCIL)]:
        response = await api.request(method, path, json=body)
        assert response.status_code == 503, path
        assert response.json()["detail"]["code"] == "mese_unreachable" and response.json()["detail"]["message"]


# ── sending ──────────────────────────────────────────────────────────────────


async def test_stream_v2_persists_the_council_and_the_turn(api, mock):
    conv = await _new_conversation(api)
    response = await api.post(f"/api/conversations/{conv['id']}/message/stream/v2", json={"content": "Hi?", "council": COUNCIL})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types[0] == "council_started" and types[-2:] == ["run_summary", "complete"]
    assert {"member_complete", "stage1_complete", "stage2_complete", "final_ready", "stage3_complete",
            "title_complete"} <= set(types)
    saved = (await api.get(f"/api/conversations/{conv['id']}")).json()
    assert saved["council"]["members"] == COUNCIL["members"] and saved["council"]["chairman"] == OPUS
    assert saved["council"]["preset_id"] is None and saved["council"]["updated_at"].endswith("Z")
    assert saved["title"] == "Test Title"
    user, assistant = saved["messages"]
    assert user == {"role": "user", "content": "Hi?"}
    assert {"stage1", "stage2", "stage3", "metadata", "council_snapshot", "failures", "usage", "status"} <= set(assistant)
    assert assistant["status"] == "complete" and assistant["metadata"]["label_to_model"]["Response A"] == OPUS
    assert {c["conversation_id"] for c in chat_calls(mock)} == {conv["id"]}


async def test_non_streaming_message_returns_the_turn(api):
    conv = await _new_conversation(api, council=COUNCIL)
    response = await api.post(f"/api/conversations/{conv['id']}/message", json={"content": "Hi?"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"stage1", "stage2", "stage3", "metadata", "failures", "usage"}
    assert [e["model"] for e in body["stage1"]] == COUNCIL["members"] and body["stage3"]["error"] is None


async def test_a_title_is_generated_while_the_conversation_has_the_default_title(api, mock, settings):
    """A first run that was cancelled left only the user message; the next turn still names the conversation."""
    original = json.loads((CONVERSATION_FIXTURES / "legacy_user_only.json").read_text())
    target = Path(settings.conversations_dir)
    target.mkdir(parents=True)
    shutil.copyfile(CONVERSATION_FIXTURES / "legacy_user_only.json", target / f"{original['id']}.json")
    response = await api.post(f"/api/conversations/{original['id']}/message/stream/v2",
                              json={"content": "Again?", "council": COUNCIL})
    assert "title_complete" in [e["type"] for e in parse_sse(response.text)]
    assert (await api.get(f"/api/conversations/{original['id']}")).json()["title"] == "Test Title"
    mock.requests.clear()
    await api.post(f"/api/conversations/{original['id']}/message", json={"content": "Third?"})
    assert "title" not in [c["stage"] for c in chat_calls(mock)]  # named once, never again


async def test_a_failed_title_is_not_retried_on_every_later_turn(api, mock):
    """RV-CD-1: a finished title call that failed marks the conversation; later turns pay no title call and
    are 'complete', not 'partial'."""
    route_member(mock, OPUS, title="")  # the chairman names the conversation; an empty answer is a failure
    conv = await _new_conversation(api, council=COUNCIL)
    first = await api.post(f"/api/conversations/{conv['id']}/message/stream/v2", json={"content": "One?"})
    assert "title_complete" in [e["type"] for e in parse_sse(first.text)]
    for content in ("Two?", "Three?"):
        assert (await api.post(f"/api/conversations/{conv['id']}/message", json={"content": content})).status_code == 200
    assert [c["stage"] for c in chat_calls(mock)].count("title") == 1
    saved = (await api.get(f"/api/conversations/{conv['id']}")).json()
    assert saved["title"] == "New Conversation" and saved["title_attempted"] is True
    statuses = [m["status"] for m in saved["messages"] if m["role"] == "assistant"]
    assert statuses == ["partial", "complete", "complete"]
    assert [f["stage"] for f in saved["messages"][1]["failures"]] == ["title"]


async def test_saved_council_and_its_preset_id_are_used_on_the_next_send(api, mock):
    conv = await _new_conversation(api)
    put = await api.put(f"/api/conversations/{conv['id']}/council", json={**COUNCIL, "preset_id": "my-team"})
    assert put.status_code == 200
    council = put.json()["council"]
    assert council["members"] == COUNCIL["members"] and council["preset_id"] == "my-team"
    await api.post(f"/api/conversations/{conv['id']}/message/stream/v2", json={"content": "Hi?"})
    assert {c["model"] for c in chat_calls(mock) if c["stage"] == "stage1"} == set(COUNCIL["members"])
    saved = (await api.get(f"/api/conversations/{conv['id']}")).json()["council"]
    assert saved["preset_id"] == "my-team"  # unchanged council keeps its preset
    await api.post(f"/api/conversations/{conv['id']}/message/stream/v2",
                   json={"content": "Again", "council": {"members": [OPUS, GPT], "chairman": OPUS}})
    saved = (await api.get(f"/api/conversations/{conv['id']}")).json()["council"]
    assert saved["members"] == [OPUS, GPT] and saved["preset_id"] is None


async def test_put_council_validates_and_404s(api):
    assert (await api.put("/api/conversations/missing-id/council", json=COUNCIL)).status_code == 404
    conv = await _new_conversation(api)
    bad = await api.put(f"/api/conversations/{conv['id']}/council", json={"members": ["x/unknown"], "chairman": OPUS})
    assert bad.status_code == 422 and bad.json()["detail"]["bad_ids"] == ["x/unknown"]


async def test_default_council_falls_back_to_builtin_hybrid(api, mock):
    conv = await _new_conversation(api)
    response = await api.post(f"/api/conversations/{conv['id']}/message/stream/v2", json={"content": "Hi?"})
    assert response.status_code == 200
    council = (await api.get(f"/api/conversations/{conv['id']}")).json()["council"]
    assert council["members"] == [SONNET, GPT, QWEN, VLLM] and council["chairman"] == OPUS
    assert council["preset_id"] == "builtin:hybrid"


async def test_env_default_council_wins_over_hybrid(mock, tmp_path):
    settings = make_settings(tmp_path, default_council=(QWEN, GPT_OSS), default_chairman=GPT_OSS)
    mese = make_client(mock)
    app = create_app(settings, mese_client=mese)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as api:
        conv = await _new_conversation(api)
        assert (await api.post(f"/api/conversations/{conv['id']}/message", json={"content": "x"})).status_code == 200
        council = (await api.get(f"/api/conversations/{conv['id']}")).json()["council"]
    await mese.aclose()
    assert (council["members"], council["chairman"], council["preset_id"]) == ([QWEN, GPT_OSS], GPT_OSS, None)


async def test_council_required_when_nothing_resolves(api, mock):
    mock.models = {"object": "list", "data": []}
    conv = await _new_conversation(api)
    response = await api.post(f"/api/conversations/{conv['id']}/message", json={"content": "hi"})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "council_required"


async def test_create_conversation_with_council_or_preset(api):
    with_council = await _new_conversation(api, council=COUNCIL, preset_id="builtin:hybrid")
    assert with_council["council"]["members"] == COUNCIL["members"]
    assert with_council["council"]["preset_id"] == "builtin:hybrid"
    local = await _new_conversation(api, preset_id="builtin:all-local")
    assert local["council"]["chairman"] == GPT_OSS and local["council"]["preset_id"] == "builtin:all-local"
    assert (await _new_conversation(api))["council"] is None
    assert (await api.post("/api/conversations")).json()["council"] is None
    missing = await api.post("/api/conversations", json={"preset_id": "nope"})
    assert missing.status_code == 404 and missing.json()["detail"]["code"] == "preset_not_found"


async def test_v1_message_stream_is_gone(api):
    conv = await _new_conversation(api)
    response = await api.post(f"/api/conversations/{conv['id']}/message/stream", json={"content": "hi"})
    assert response.status_code == 404


async def test_unknown_or_unsafe_conversation_ids_are_404(api):
    for conversation_id in ("does-not-exist", "..", "..%2F..%2Fetc%2Fpasswd", ".hidden"):
        assert (await api.get(f"/api/conversations/{conversation_id}")).status_code == 404


# ── catalog, health, plan ────────────────────────────────────────────────────


async def test_models_refresh_skips_the_catalog_cache_for_the_presets_too(api, mock):
    fetches = lambda: len([r for r in mock.requests if r.url.path == "/v1/models"])  # noqa: E731
    assert (await api.get("/api/models")).status_code == 200 and (await api.get("/api/models")).status_code == 200
    assert fetches() == 1  # 10 s cache
    assert (await api.get("/api/models?refresh=1")).status_code == 200 and fetches() == 2
    assert (await api.get("/api/presets")).status_code == 200 and fetches() == 2  # presets see the fresh list


async def test_models_projection_and_no_token(api, mock):
    response = await api.get("/api/models")
    assert response.status_code == 200
    assert TEST_MESE_TOKEN not in response.text and "base_url" not in response.text
    body = response.json()
    assert body["mese"] == {"ok": True, "admin_url": "http://127.0.0.1:8791/admin/"}
    assert [m["id"] for m in body["models"]] == [m["id"] for m in mock.models["data"]]
    opus = next(m for m in body["models"] if m["id"] == OPUS)
    assert opus["price_per_mtok"] == {"input": 4.0, "output": 20.0} and opus["provider"] == {"id": "anthropic", "name": "Anthropic"}
    ft = next(m for m in body["models"] if m["id"] == FT)
    assert (ft["kind"], ft["price_per_mtok"], ft["lineage"], ft["can_rank"]) == ("custom", None, {"name": "legal-qwen", "version": "v3"}, False)
    assert set(opus) == {"id", "name", "kind", "provider", "node", "available", "unavailable_reason", "load_state",
                         "can_rank", "can_chair", "context_length", "size_bytes", "price_per_mtok", "price_verified",
                         "tags", "lineage"}


async def test_health_reports_status_without_the_token(api, mock):
    response = await api.get("/api/mese/health")
    assert response.status_code == 200 and TEST_MESE_TOKEN not in response.text
    health = response.json()
    assert set(health) == {"ok", "url", "admin_url", "token_set", "latency_ms", "version", "locked", "nodes_up",
                           "nodes_total", "providers_configured", "breakers_open", "error"}
    assert (health["ok"], health["token_set"], health["version"], health["nodes_up"], health["error"]) == (True, True, "0.1.0", 2, None)
    mock.unreachable = True
    down = (await api.get("/api/mese/health")).json()
    assert (down["ok"], down["latency_ms"], down["version"]) == (False, None, None) and "unreachable" in down["error"]


async def test_health_without_token(mock, tmp_path):
    settings = make_settings(tmp_path, mese_api_key=None)
    mese = make_client(mock, token=None)
    app = create_app(settings, mese_client=mese)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as api:
        health = (await api.get("/api/mese/health")).json()
    await mese.aclose()
    assert (health["ok"], health["token_set"]) == (False, False) and "MESE_API_KEY" in health["error"]


async def test_plan_is_proxied(api, mock):
    response = await api.post("/api/council/plan", json={"members": [QWEN, GPT_OSS], "chairman": QWEN})
    assert response.status_code == 200 and response.json() == mock.plan
    sent = [json.loads(r.content) for r in mock.requests if r.url.path == "/v1/mese/plan"]
    assert sent == [{"models": [QWEN, GPT_OSS]}]


# ── presets ──────────────────────────────────────────────────────────────────


async def test_builtin_presets_resolve_from_the_fixture_catalog(api):
    presets = {p["id"]: p for p in (await api.get("/api/presets")).json()["presets"]}
    assert (presets["builtin:all-local"]["members"], presets["builtin:all-local"]["chairman"]) == ([QWEN, VLLM, GPT_OSS], GPT_OSS)
    assert (presets["builtin:frontier-cloud"]["members"], presets["builtin:frontier-cloud"]["chairman"]) == ([OPUS, SONNET, GPT], OPUS)
    assert (presets["builtin:hybrid"]["members"], presets["builtin:hybrid"]["chairman"]) == ([SONNET, GPT, QWEN, VLLM], OPUS)
    assert all(p["builtin"] and p["unavailable"] == [] and p["created_at"] is None for p in presets.values())


async def test_user_preset_crud(api):
    created = await api.put("/api/presets/my-team", json={"name": " My team ", "members": [OPUS, GROK], "chairman": OPUS})
    assert created.status_code == 200
    preset = created.json()
    assert (preset["id"], preset["name"], preset["builtin"], preset["unavailable"]) == ("my-team", "My team", False, [GROK])
    listed = (await api.get("/api/presets")).json()["presets"]
    assert [p["id"] for p in listed] == ["builtin:all-local", "builtin:frontier-cloud", "builtin:hybrid", "my-team"]
    updated = (await api.put("/api/presets/my-team", json={"name": "Renamed", "members": [QWEN], "chairman": QWEN})).json()
    assert updated["name"] == "Renamed" and updated["created_at"] == preset["created_at"]
    stored = json.loads(Path(api.app.state.ctx.settings.presets_path).read_text())
    assert stored["version"] == 1 and [p["id"] for p in stored["presets"]] == ["my-team"]
    assert (await api.delete("/api/presets/my-team")).status_code == 204
    assert (await api.delete("/api/presets/my-team")).status_code == 404
    assert [p["id"] for p in (await api.get("/api/presets")).json()["presets"]][-1] == "builtin:hybrid"


async def test_user_preset_validation(api):
    unknown = await api.put("/api/presets/team", json={"name": "T", "members": ["zz/unknown"], "chairman": OPUS})
    assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "unknown_models"
    assert (await api.put("/api/presets/Bad_Id", json={"name": "T", "members": [OPUS], "chairman": OPUS})).status_code == 422
    assert (await api.put("/api/presets/team", json={"name": "  ", "members": [OPUS], "chairman": OPUS})).status_code == 422


@pytest.mark.parametrize("preset_id", ["builtin:hybrid", "builtin:all-local", "builtin:anything"])
async def test_builtin_presets_are_immutable(api, preset_id):
    put = await api.put(f"/api/presets/{preset_id}", json={"name": "x", "members": [OPUS], "chairman": OPUS})
    assert put.status_code == 409 and put.json()["detail"]["code"] == "preset_immutable"
    assert (await api.put(f"/api/presets/{preset_id}", json={})).status_code == 409
    assert (await api.delete(f"/api/presets/{preset_id}")).status_code == 409


# ── legacy, CORS ─────────────────────────────────────────────────────────────


async def test_legacy_conversations_load_unchanged(api, settings):
    target = Path(settings.conversations_dir)
    target.mkdir(parents=True)
    for path in CONVERSATION_FIXTURES.glob("legacy_*.json"):  # stored as <id>.json, like real data
        shutil.copyfile(path, target / f"{json.loads(path.read_text())['id']}.json")
    listed = (await api.get("/api/conversations")).json()
    assert len(listed) == 5 and all(set(item) == {"id", "created_at", "title", "message_count"} for item in listed)
    for path in CONVERSATION_FIXTURES.glob("legacy_*.json"):
        original = json.loads(path.read_text())
        loaded = (await api.get(f"/api/conversations/{original['id']}")).json()
        assert loaded["council"] is None and loaded["messages"] == original["messages"]
        assert {k: loaded[k] for k in ("id", "created_at", "title")} == {k: original[k] for k in ("id", "created_at", "title")}


async def test_cors_is_an_explicit_list_without_credentials(api):
    (cors,) = [m for m in api.app.user_middleware if m.cls is CORSMiddleware]
    assert "allow_origin_regex" not in cors.kwargs and cors.kwargs["allow_credentials"] is False
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    ok = await api.options("/api/conversations", headers={"Origin": "http://localhost:5173", **preflight})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-credentials" not in ok.headers
    evil = await api.options("/api/conversations", headers={"Origin": "http://evil.example:5173", **preflight})
    assert evil.status_code == 400 and "access-control-allow-origin" not in evil.headers


@pytest.mark.parametrize("host", ["rebind.attacker.example:8001", "rebind.attacker.example", "127.0.0.1.nip.example:8001",
                                  "localhost.attacker.example", "[::2]:8001", "0.0.0.0:8001"])
async def test_a_host_outside_the_cors_policy_is_400(api, host):
    """DNS rebinding: a page on the attacker's name is same-origin, so CORS is moot; its Host header is not."""
    for method, path in [("GET", "/api/conversations"), ("POST", "/api/conversations"), ("GET", "/"),
                         ("POST", "/api/conversations/x/message/stream/v2")]:
        response = await api.request(method, path, headers={"host": host, "origin": f"http://{host}"}, json={})
        assert response.status_code == 400, (method, path)
        assert response.json()["detail"]["code"] == "invalid_host"
    assert api.app.state.ctx.store.list() == []


async def test_hosts_named_by_cors_loopback_and_a_specific_council_host_are_allowed(mock, tmp_path):
    origins = ("http://localhost:5173", "http://192.168.1.20:5173", "http://100.101.102.103:5173",
               "http://mac.tail1234.ts.net:5173")  # what start.sh adds: LAN and tailnet origins on :5173
    settings = make_settings(tmp_path, allowed_origins=origins, council_host="100.64.0.9")
    mese = make_client(mock)
    app = create_app(settings, mese_client=mese)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as api:
        for host in ("localhost:8001", "localhost", "127.0.0.1:8001", "[::1]:8001", "192.168.1.20:8001",
                     "100.101.102.103:8001", "MAC.tail1234.ts.net:8001", "100.64.0.9:8001"):
            assert (await api.get("/api/conversations", headers={"host": host})).status_code == 200, host
        assert (await api.get("/api/conversations", headers={"host": "192.168.1.21:8001"})).status_code == 400
    await mese.aclose()


async def test_a_wildcard_cors_origin_disables_the_host_check_and_a_missing_host_is_400(mock, tmp_path):
    mese = make_client(mock)
    open_app = create_app(make_settings(tmp_path, allowed_origins=("*",)), mese_client=mese)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=open_app), base_url=BASE_URL) as api:
        assert (await api.get("/api/conversations", headers={"host": "anything.example"})).status_code == 200
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.0", "method": "GET", "scheme": "http",
             "path": "/", "raw_path": b"/", "query_string": b"", "root_path": "", "server": ("127.0.0.1", 8001),
             "client": ("127.0.0.1", 50000), "headers": []}
    await create_app(make_settings(tmp_path), mese_client=mese)(scope, receive, send)
    await mese.aclose()
    assert sent[0]["status"] == 400


# ── client disconnect through the real ASGI path ─────────────────────────────


async def test_client_disconnect_cancels_every_in_flight_mese_stream(mock, settings):
    members = [OPUS, GPT, QWEN]
    for model in members:
        partial = build_mese_sse(model, "partial").split(b'"finish_reason":"stop"')[0]
        mock.chat_sse(model, partial.rsplit(b"\n\n", 1)[0] + b"\n\n", hang=True)
    mese = make_client(mock)
    app = create_app(settings, mese_client=mese)
    conv = app.state.ctx.store.create("conv-disconnect")
    body = json.dumps({"content": "hi", "council": COUNCIL}).encode()
    disconnected = asyncio.Event()
    sent = []
    requested = False

    async def receive():
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            streaming = [e for e in parse_sse(b"".join(m.get("body", b"") for m in sent[1:]).decode())
                         if e["type"] == "member_streaming"]
            if len(streaming) == 3:
                disconnected.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": f"/api/conversations/{conv['id']}/message/stream/v2",
             "raw_path": b"", "query_string": b"", "root_path": "", "server": ("127.0.0.1", 8001),
             "client": ("127.0.0.1", 50000), "headers": [(b"content-type", b"application/json"), (b"host", b"127.0.0.1:8001")]}
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    for _ in range(200):
        if len(mock.closed_streams()) == 3:
            break
        await asyncio.sleep(0.005)
    assert sent[0]["status"] == 200
    assert len(mock.streams) == 3 and len(mock.closed_streams()) == 3
    user, assistant = app.state.ctx.store.get(conv["id"])["messages"]  # the cancelled turn is stored, not orphaned
    assert user["content"] == "hi" and assistant["status"] == "cancelled"
    assert {f["model"] for f in assistant["failures"]} == set(COUNCIL["members"])
    await mese.aclose()

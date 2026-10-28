"""WP0 contract checks for llm-council: Python contracts, request schemas, golden fixtures,
the MockTransport replay helpers and the network guard. Owned by WP0 (lives under tests/fixtures/
so it never collides with WP7's tests/test_*.py)."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from backend.contracts import SSE_EVENTS, MemberResult
from backend.schemas import (BUILTIN_PRESET_IDS, ConversationCouncilPut, CouncilConfig, CreateConversationRequest, PresetPutRequest,
                             SendMessageRequest, is_user_preset_id)
from conftest import (CONVERSATION_FIXTURES, FRONTEND_FIXTURES, MESE_FIXTURES, TEST_MESE_TOKEN, TEST_MESE_URL, BlockedNetworkError,
                      MeseMock, build_mese_sse, load_fixture_bytes, make_mese_transport)

# ── contracts & schemas ──────────────────────────────────────────────────────


def test_contracts_shape():
    assert len(SSE_EVENTS) == 24 and len(set(SSE_EVENTS)) == 24
    r = MemberResult(model="a/b", ok=False)
    assert r.content == "" and r.warnings == [] and r.retryable is False


@pytest.mark.parametrize("members,chairman", [
    ([], "a/b"),
    ([f"a/m{i}" for i in range(9)], "a/b"),
    (["a/x", "a/x"], "a/b"),
    (["Bad/x"], "a/b"),
    (["a/x"], "no-slash"),
])
def test_council_config_rejects(members, chairman):
    with pytest.raises(ValidationError):
        CouncilConfig(members=members, chairman=chairman)


def test_council_config_requires_chairman_and_accepts_real_ids():
    with pytest.raises(ValidationError):
        CouncilConfig(members=["a/x"])  # type: ignore[call-arg]
    ids = ["anthropic/claude-opus-5-5", "spark/qwen3.8:27b", "ft/legal-qwen-v3", "spark-vllm/Qwen/Qwen3.8-27B"] + [f"p/m{i}" for i in range(4)]
    cfg = CouncilConfig(members=ids, chairman="spark/gpt-oss:120b")
    assert cfg.as_dict() == {"members": ids, "chairman": "spark/gpt-oss:120b"}


def test_request_models():
    assert SendMessageRequest(content="hi").council is None
    with pytest.raises(ValidationError):
        SendMessageRequest(content="")
    assert CreateConversationRequest(preset_id="builtin:hybrid").preset_id == "builtin:hybrid"
    assert ConversationCouncilPut(members=["a/b"], chairman="a/b", preset_id="mine").preset_id == "mine"
    assert PresetPutRequest(name="  Mine ", members=["a/b"], chairman="a/b").name == "Mine"
    with pytest.raises(ValidationError):
        PresetPutRequest(name="   ", members=["a/b"], chairman="a/b")
    assert all(not is_user_preset_id(b) for b in BUILTIN_PRESET_IDS) and is_user_preset_id("my-preset")


# ── golden Mese transcripts ──────────────────────────────────────────────────


def _reference_member_result(model: str, raw: bytes):
    """Compact reference of the §I.2 mapping, used only to prove expected.json matches the transcripts."""
    statuses, deltas, finish, thinking = [], [], None, False
    for frame in raw.decode().split("\n\n"):
        lines = [l for l in frame.split("\n") if l and not l.startswith(":")]
        if not lines:
            continue
        event = next((l[7:] for l in lines if l.startswith("event: ")), None)
        data = "\n".join(l[6:] for l in lines if l.startswith("data: "))
        if data == "[DONE]":
            break
        obj = json.loads(data)
        if event == "mese":
            t = obj["type"]
            if t == "queued":
                statuses.append({"state": "queued", "position": obj["position"], "node_id": obj["node_id"], "reason": obj["reason"]})
            elif t == "loading":
                statuses.append({"state": "loading", "node_id": obj["node_id"]})
            elif t == "started":
                statuses.append({"state": "started", "attempt": obj["attempt"]})
            elif t == "retrying":
                statuses.append({"state": "retrying", "attempt": obj["attempt"], "reason": obj["reason"]})
            continue
        delta = obj["choices"][0]["delta"] if obj["choices"] else {}
        if delta.get("reasoning_content") and not thinking:
            thinking = True
            statuses.append({"state": "thinking"})
        if delta.get("content"):
            deltas.append(delta["content"])
        if obj["choices"] and obj["choices"][0]["finish_reason"]:
            finish = obj
    out = {"model": model, "content": "".join(deltas), "deltas": deltas, "statuses": statuses}
    if finish is None:
        return {**out, "ok": False, "error_code": "upstream_error", "retryable": True, "finish_reason": None}
    fr, summary, u = finish["choices"][0]["finish_reason"], finish["mese"], finish["usage"]
    out.update(finish_reason=fr, cost_usd=summary["cost_usd"], ttft_ms=summary["ttft_ms"], warnings=summary["warnings"], resolved=summary["resolved"],
               usage={"prompt_tokens": u["prompt_tokens"], "completion_tokens": u["completion_tokens"],
                      "reasoning_tokens": u["completion_tokens_details"]["reasoning_tokens"]})
    if fr in ("stop", "length"):
        out.update(ok=True, error_code=None, error_message=None, retryable=False)
    elif fr == "content_filter":
        cat = next((w.split(":", 1)[1] for w in summary["warnings"] if w.startswith("refusal:")), "unknown")
        out.update(ok=False, error_code="content_filtered", error_message=f"declined by provider ({cat})", retryable=False)
    else:
        out.update(ok=False, error_code=finish["error"]["code"], error_message=finish["error"]["message"], retryable=finish["error"]["mese"]["retryable"])
    return out


def test_expected_member_results_match_transcripts(mese_expected):
    for name, exp in mese_expected.items():
        if name.endswith(".sse"):
            raw = load_fixture_bytes(name)
            if name != "truncated_no_finish.sse":
                assert raw.endswith(b"data: [DONE]\n\n"), name
            got = _reference_member_result(exp["model"], raw)
            assert {k: got.get(k) for k in exp} == exp, name
        elif name.endswith(".json"):
            f = json.loads(load_fixture_bytes(name))
            body = f["body"]["error"]
            assert (f["status"] >= 400, body["code"], body["message"], body["mese"]["retryable"]) == \
                (True, exp["error_code"], exp["error_message"], exp["retryable"]), name


def test_every_fixture_parses():
    for p in MESE_FIXTURES.glob("*.json"):
        json.loads(p.read_text())
    models = json.loads((MESE_FIXTURES / "v1_models.json").read_text())
    assert models["object"] == "list" and {m["mese"]["kind"] for m in models["data"]} == {"cloud", "local", "custom"}
    assert all("base_url" not in json.dumps(m) for m in models["data"])


def test_legacy_conversations_have_the_old_shapes(legacy_conversations):
    assert {"legacy_empty.json", "legacy_openrouter_v1.json", "legacy_openrouter_v2_latency.json", "legacy_all_failed.json",
            "legacy_user_only.json"} <= set(legacy_conversations)
    assert legacy_conversations["legacy_empty.json"]["messages"] == []
    for conv in legacy_conversations.values():
        assert set(conv) == {"id", "created_at", "title", "messages"}
        for m in conv["messages"]:
            if m["role"] == "assistant":
                assert set(m) == {"role", "stage1", "stage2", "stage3"}
                assert all(set(e) <= {"model", "response", "latency_ms"} for e in m["stage1"])
                assert all(set(e) == {"model", "ranking", "parsed_ranking"} for e in m["stage2"])
    assert CONVERSATION_FIXTURES.is_dir()


def test_frontend_event_fixtures(frontend_event_fixtures):
    assert set(frontend_event_fixtures) == {"hybrid-success", "member-failure", "stage2-skipped", "ranker-context-skip",
                                            "final-error", "all-local-queue"}
    seen = set()
    for name, fx in frontend_event_fixtures.items():
        CouncilConfig(**fx["council"])
        types = [e["type"] for e in fx["events"]]
        seen.update(types)
        assert set(types) <= set(SSE_EVENTS), name
        assert types[0] == "council_started" and types[-2:] == ["run_summary", "complete"], name
        assert types.index("stage1_complete") < types.index("stage2_complete") < types.index("stage3_complete"), name
    assert seen == set(SSE_EVENTS) - {"error"}
    assert FRONTEND_FIXTURES.is_dir()


# ── MockTransport replay & network guard ─────────────────────────────────────


async def test_mese_mock_replays_and_records():
    mock = make_mese_transport({"spark/qwen3.8:27b": "events.sse", "google/gemini-4-argon": "pre_stream_error.json"})
    async with httpx.AsyncClient(base_url=TEST_MESE_URL, transport=mock.transport, headers={"Authorization": f"Bearer {TEST_MESE_TOKEN}"}) as c:
        async with c.stream("POST", "/v1/chat/completions", json={"model": "spark/qwen3.8:27b", "messages": [], "stream": True}) as r:
            body = b"".join([chunk async for chunk in r.aiter_bytes()])
        assert body == load_fixture_bytes("events.sse")
        err = await c.post("/v1/chat/completions", json={"model": "google/gemini-4-argon", "messages": []})
        assert err.status_code == 424 and err.json()["error"]["code"] == "credential_missing"
        assert (await c.get("/v1/models")).json()["object"] == "list"
        assert (await c.post("/v1/chat/completions", json={"model": "nope/x", "messages": []})).status_code == 404
    assert mock.chat_bodies()[0]["model"] == "spark/qwen3.8:27b"
    assert len(mock.requests) == 4


async def test_mese_mock_records_early_close_auth_and_unreachable():
    mock = MeseMock()
    mock.chat_sse("spark/qwen3.8:27b", "events.sse", delay=0.01, hang=True)
    async with httpx.AsyncClient(base_url=TEST_MESE_URL, transport=mock.transport, headers={"Authorization": f"Bearer {TEST_MESE_TOKEN}"}) as c:
        async with c.stream("POST", "/v1/chat/completions", json={"model": "spark/qwen3.8:27b", "messages": []}) as r:
            async for _ in r.aiter_bytes():
                break
        assert len(mock.closed_streams()) == 1
        assert (await c.get("/v1/models", headers={"Authorization": "Bearer wrong"})).status_code == 401
        mock.unreachable = True
        with pytest.raises(httpx.ConnectError):
            await c.get("/v1/mese/status")


def test_build_mese_sse_matches_the_golden_format():
    raw = build_mese_sse("ft/legal-qwen-v3", "Hello", reasoning="hmm", cost_usd=0)
    got = _reference_member_result("ft/legal-qwen-v3", raw)
    assert got["ok"] and got["content"] == "Hello" and got["statuses"] == [{"state": "started", "attempt": 1}, {"state": "thinking"}]
    err = _reference_member_result("xai/grok-4.7", build_mese_sse("xai/grok-4.7", "x", finish_reason="error",
                                                                    error={"code": "upstream_timeout", "message": "slow"}))
    assert (err["ok"], err["error_code"], err["retryable"]) == (False, "upstream_timeout", True)


def test_network_guard_blocks_non_loopback():
    with pytest.raises(BlockedNetworkError):
        socket.getaddrinfo("api.openai.com", 443)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(BlockedNetworkError):
            s.connect(("10.255.255.1", 443))
    finally:
        s.close()
    with pytest.raises(httpx.ConnectError):
        httpx.get("https://api.openai.com/v1/models", timeout=2)

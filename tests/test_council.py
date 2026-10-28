"""Council runner (§I.4) against the mock Mese: events, failures, Stage-2 rules, title privacy, cancellation."""
from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest

from backend.council import run_council
from backend.council.runner import CONTEXT_RESERVE_TOKENS, fits_context
from backend.main import council_event_stream
from backend.storage import ConversationStore
from conftest import MeseMock, ReplayStream, build_mese_sse, split_frames
from test_support import (FT, GPT, GPT_OSS, GROK, HAIKU, OPUS, QWEN, VLLM, EventLog, chat_calls, make_deps,
                          parse_sse, patch_model, route_http_error, route_member)

QUERY = "SQLite or PostgreSQL for a single-user app?"


@pytest.fixture
def store(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    store.create("conv-1")
    store.add_user_message("conv-1", QUERY)
    return store


async def _run(mock, store, council, *, first=True, title_model=None, log=None):
    log = log or EventLog()
    deps = make_deps(mock, store, title_model=title_model)
    try:
        result = await run_council(QUERY, council, "conv-1", log, deps=deps, is_first_message=first)
    finally:
        await deps.client.aclose()
    return result, log


def _persisted(store):
    return store.get("conv-1")["messages"][-1]


async def test_three_member_run_with_one_failure(store):
    mock = MeseMock()
    route_member(mock, OPUS, cost_usd=0.004)
    route_member(mock, QWEN, cost_usd=0.0)
    route_http_error(mock, GROK, 503, "circuit_open", "breaker for provider:xai is open", True)
    result, log = await _run(mock, store, {"members": [OPUS, GROK, QWEN], "chairman": OPUS})

    assert log.types[0] == "council_started" and log.types[-2:] == ["run_summary", "complete"]
    assert log.types[1:4] == ["member_queued"] * 3
    order = ["stage1_complete", "stage2_start", "stage2_complete", "final_generating", "final_ready",
             "stage3_complete", "title_complete", "run_summary"]
    assert [log.index(t) for t in order] == sorted(log.index(t) for t in order)
    error = log.one("member_error", memberId=GROK)
    assert error == {"type": "member_error", "memberId": GROK, "message": "breaker for provider:xai is open",
                     "code": "circuit_open", "retryable": True}
    assert {e["memberId"] for e in log.of("member_complete")} == {OPUS, QWEN}
    complete = log.one("member_complete", memberId=OPUS)
    assert complete["costUsd"] == 0.004 and complete["usage"] == {"promptTokens": 10, "completionTokens": 5,
                                                                    "reasoningTokens": 0}
    assert set(complete) == {"type", "memberId", "response", "latencyMs", "ttftMs", "costUsd", "usage"}

    failure = {"stage": "1", "model": GROK, "code": "circuit_open", "message": "breaker for provider:xai is open"}
    assert result.failures == [failure] and result.status == "partial"
    message = _persisted(store)
    assert message["failures"] == [failure] and message["status"] == "partial"
    assert [e["model"] for e in message["stage1"]] == [OPUS, QWEN]  # Stage-1 success order (member order)
    assert all(set(e) == {"model", "response", "latency_ms", "cost_usd"} for e in message["stage1"])
    assert all(set(e) == {"model", "ranking", "parsed_ranking"} for e in message["stage2"])
    assert message["metadata"]["label_to_model"] == {"Response A": OPUS, "Response B": QWEN}
    assert message["metadata"]["aggregate_rankings"][0] == {"model": QWEN, "average_rank": 1.0, "rankings_count": 2}
    assert log.one("stage2_complete")["metadata"]["failures"] == [failure]
    summary = log.one("run_summary")
    assert summary["failures"] == [failure] and summary["status"] == "partial" and summary["runId"] == result.run_id
    assert summary["tokens"] == {"prompt": message["usage"]["prompt_tokens"], "completion": message["usage"]["completion_tokens"]}


async def test_every_call_carries_run_id_conversation_and_stage(store):
    mock = MeseMock()
    for model in (OPUS, GPT):
        route_member(mock, model)
    result, _ = await _run(mock, store, {"members": [OPUS, GPT], "chairman": OPUS})
    calls = chat_calls(mock)
    assert re.fullmatch(r"run_[0-9a-f]{32}", result.run_id)
    assert {c["run_id"] for c in calls} == {result.run_id} and {c["conversation_id"] for c in calls} == {"conv-1"}
    assert [c["stage"] for c in calls] == ["stage1", "stage1", "stage2", "stage2", "stage3", "title"]
    assert all(c["body"]["mese"]["client"] == "llm-council" and c["body"]["mese"]["events"] is True for c in calls)
    assert not any(k in c["body"] for c in calls for k in ("max_tokens", "temperature"))


async def test_member_status_maps_mese_frames_and_reasoning(store):
    mock = MeseMock()
    mock.chat_sse(QWEN, "events.sse")
    route_member(mock, OPUS)
    _, log = await _run(mock, store, {"members": [QWEN, OPUS], "chairman": OPUS}, first=False)
    statuses = [{k: v for k, v in e.items() if k not in ("type", "memberId")} for e in log.of("member_status", memberId=QWEN)]
    assert statuses == [{"state": "queued", "position": 2, "nodeId": "spark", "reason": "memory"},
                        {"state": "loading", "nodeId": "spark"}, {"state": "started", "attempt": 1}, {"state": "thinking"}]
    assert [e["deltaText"] for e in log.of("member_streaming", memberId=QWEN)] == ["The answer is", " 4."]
    started = log.one("council_started")
    assert started["members"][0] == {"memberId": QWEN, "displayName": "Qwen3.8 27B", "kind": "local", "providerId": None,
                                     "nodeId": "spark", "canRank": True}
    assert started["chairman"] == {"modelId": OPUS, "displayName": "Claude Opus 5.5", "kind": "cloud",
                                   "providerId": "anthropic", "nodeId": None}


async def test_can_rank_false_members_are_excluded_from_stage2(store):
    mock = MeseMock()
    for model in (OPUS, QWEN, FT):
        route_member(mock, model)
    result, log = await _run(mock, store, {"members": [OPUS, QWEN, FT], "chairman": OPUS}, first=False)
    stages_by_model = [(c["model"], c["stage"]) for c in chat_calls(mock)]
    assert (FT, "stage1") in stages_by_model and (FT, "stage2") not in stages_by_model
    assert {e["memberId"] for e in log.of("stage2_member_status")} == {OPUS, QWEN}
    assert [e["model"] for e in result.stage2] == [OPUS, QWEN]
    assert FT in result.metadata["label_to_model"].values()  # it still answered in Stage 1


async def test_fewer_than_two_successes_skip_stage2(store):
    mock = MeseMock()
    route_member(mock, OPUS)
    mock.chat_http_error(GROK, "pre_stream_error.json")
    result, log = await _run(mock, store, {"members": [OPUS, GROK], "chairman": OPUS}, first=False)
    assert log.one("stage2_skipped") == {"type": "stage2_skipped", "reason": "fewer_than_two_responses"}
    assert log.index("stage2_skipped") + 1 == log.index("stage2_complete")
    assert "stage2_start" not in log.types
    complete = log.one("stage2_complete")
    assert complete["data"] == [] and complete["metadata"]["label_to_model"] == {}
    assert complete["metadata"]["failures"][0]["code"] == "credential_missing"
    assert not [c for c in chat_calls(mock) if c["stage"] == "stage2"]
    assert log.one("final_ready")["model"] == OPUS and result.stage3["error"] is None


async def test_no_rankers_skips_stage2(store):
    mock = MeseMock()
    patch_model(mock, QWEN, can_rank=False)
    for model in (FT, QWEN):
        route_member(mock, model)
    route_member(mock, GPT_OSS)
    _, log = await _run(mock, store, {"members": [FT, QWEN], "chairman": GPT_OSS}, first=False)
    assert log.one("stage2_skipped")["reason"] == "no_rankers"
    assert log.one("stage2_complete")["data"] == []


async def test_context_overflow_ranker_is_skipped(store):
    mock = MeseMock()
    patch_model(mock, VLLM, context_length=CONTEXT_RESERVE_TOKENS + 50)
    for model in (OPUS, QWEN, VLLM):
        route_member(mock, model)
    result, log = await _run(mock, store, {"members": [OPUS, QWEN, VLLM], "chairman": OPUS}, first=False)
    assert log.one("stage2_member_skipped") == {"type": "stage2_member_skipped", "memberId": VLLM, "reason": "context"}
    assert log.index("stage2_start") < log.index("stage2_member_skipped") < log.index("stage2_complete")
    assert (VLLM, "stage2") not in [(c["model"], c["stage"]) for c in chat_calls(mock)]
    assert [e["model"] for e in result.stage2] == [OPUS, QWEN]
    assert VLLM in result.metadata["label_to_model"].values() and result.status == "complete"


def test_context_precheck_formula():
    prompt = "x" * 4000  # ceil(4000/4) = 1000 tokens
    assert fits_context(prompt, None)
    assert fits_context(prompt, 1000 + CONTEXT_RESERVE_TOKENS)
    assert not fits_context(prompt, 999 + CONTEXT_RESERVE_TOKENS)


async def test_stage2_ranker_failure_is_reported_and_the_stage_completes(store):
    mock = MeseMock()
    route_member(mock, OPUS)
    route_member(mock, QWEN, stage2={"content": "", "finish_reason": "error",
                                      "error": {"code": "upstream_error", "message": "upstream stream ended unexpectedly"}})
    result, log = await _run(mock, store, {"members": [OPUS, QWEN], "chairman": OPUS}, first=False)
    assert log.one("stage2_member_error") == {"type": "stage2_member_error", "memberId": QWEN, "code": "upstream_error",
                                              "message": "upstream stream ended unexpectedly"}
    assert [e["model"] for e in result.stage2] == [OPUS]
    assert result.failures == [{"stage": "2", "model": QWEN, "code": "upstream_error",
                                "message": "upstream stream ended unexpectedly"}]


async def test_chairman_failure_gives_final_error_and_no_fake_text(store):
    mock = MeseMock()
    route_member(mock, QWEN)
    route_member(mock, OPUS, stage3={"content": "Use SQLite ", "finish_reason": "error",
                                     "error": {"code": "upstream_timeout", "message": "upstream produced no data for 120s"}})
    result, log = await _run(mock, store, {"members": [OPUS, QWEN], "chairman": OPUS})
    error = {"code": "upstream_timeout", "message": "upstream produced no data for 120s"}
    assert log.one("final_error") == {"type": "final_error", **error}
    assert "final_ready" not in log.types
    assert log.one("stage3_complete")["data"] == {"model": OPUS, "response": "", "cost_usd": 0.0, "error": error}
    assert log.index("final_error") < log.index("stage3_complete") < log.index("title_complete")
    persisted = _persisted(store)
    assert persisted["stage3"] == {"model": OPUS, "response": "", "cost_usd": 0.0, "error": error}  # a failed call may cost
    assert {"stage": "3", "model": OPUS, **error} in persisted["failures"] and persisted["status"] == "partial"
    assert "Unable to generate" not in json.dumps(persisted)


async def test_no_stage1_success_skips_the_chairman(store):
    mock = MeseMock()
    route_http_error(mock, GROK, 503, "circuit_open", "open", True)
    route_member(mock, OPUS, stage1={"content": "", "finish_reason": "content_filter", "warnings": ["refusal:cyber"]})
    result, log = await _run(mock, store, {"members": [OPUS, GROK], "chairman": OPUS}, first=False)
    assert log.one("final_error")["code"] == "no_responses"
    assert "final_generating" not in log.types
    assert result.stage3["error"]["code"] == "no_responses" and result.stage3["response"] == ""
    assert not [c for c in chat_calls(mock) if c["stage"] == "stage3"]
    assert log.one("member_error", memberId=OPUS)["code"] == "content_filtered"


async def test_title_runs_after_stage3_and_is_persisted(store):
    mock = MeseMock()
    for model in (OPUS, GPT):
        route_member(mock, model, title='"SQLite vs Postgres"\nextra line')
    result, log = await _run(mock, store, {"members": [OPUS, GPT], "chairman": OPUS})
    assert chat_calls(mock)[-1]["stage"] == "title" and chat_calls(mock)[-1]["model"] == OPUS
    assert log.index("stage3_complete") < log.index("title_complete") < log.index("run_summary")
    assert log.one("title_complete")["data"] == {"title": "SQLite vs Postgres"}
    assert store.get("conv-1")["title"] == "SQLite vs Postgres" and result.title == "SQLite vs Postgres"


async def test_title_failure_falls_back(store):
    mock = MeseMock()
    route_member(mock, OPUS, title={"content": "", "finish_reason": "error", "error": {"code": "internal", "message": "x"}})
    route_member(mock, GPT)
    result, log = await _run(mock, store, {"members": [OPUS, GPT], "chairman": OPUS})
    assert log.one("title_complete")["data"]["title"] == "New Conversation"
    assert result.failures[-1]["stage"] == "title"


async def test_no_title_for_later_messages(store):
    mock = MeseMock()
    for model in (OPUS, GPT):
        route_member(mock, model)
    _, log = await _run(mock, store, {"members": [OPUS, GPT], "chairman": OPUS}, first=False)
    assert "title_complete" not in log.types and "title" not in [c["stage"] for c in chat_calls(mock)]


async def test_all_local_council_never_uses_a_cloud_title_model(store):
    mock = MeseMock()
    for model in (QWEN, GPT_OSS):
        route_member(mock, model)
    route_member(mock, HAIKU)
    await _run(mock, store, {"members": [QWEN, GPT_OSS], "chairman": GPT_OSS}, title_model=HAIKU)
    assert HAIKU not in [c["model"] for c in chat_calls(mock)]
    assert chat_calls(mock)[-1] == {**chat_calls(mock)[-1], "model": GPT_OSS, "stage": "title"}


@pytest.mark.parametrize("council,title_model,expected", [
    ({"members": [OPUS, QWEN], "chairman": QWEN}, HAIKU, HAIKU),  # council has a cloud model
    ({"members": [QWEN, GPT_OSS], "chairman": GPT_OSS}, VLLM, VLLM),  # local title model is fine
    ({"members": [QWEN, GPT_OSS], "chairman": GPT_OSS}, "unknown/cloud-model", GPT_OSS),  # unknown counts as cloud
])
async def test_title_model_choice(store, council, title_model, expected):
    mock = MeseMock()
    for model in {*council["members"], council["chairman"], title_model}:
        route_member(mock, model)
    await _run(mock, store, council, title_model=title_model)
    assert chat_calls(mock)[-1]["model"] == expected


async def test_council_snapshot_and_usage_are_stored(store):
    mock = MeseMock()
    route_member(mock, OPUS, cost_usd=0.01)
    route_member(mock, FT, cost_usd=0.0)
    route_member(mock, QWEN, cost_usd=None)
    result, log = await _run(mock, store, {"members": [OPUS, FT, QWEN], "chairman": OPUS})
    snapshot = _persisted(store)["council_snapshot"]
    assert snapshot["run_id"] == result.run_id
    ft = next(m for m in snapshot["members"] if m["id"] == FT)
    assert ft == {"id": FT, "display_name": "Legal Qwen v3", "kind": "custom", "provider_id": None, "node_id": "spark",
                  "upstream_model": "legal-qwen-v3:latest", "can_rank": False,
                  "lineage": {"name": "legal-qwen", "version": "v3", "training_run_id": "run-2026-10-20-a"}}
    assert snapshot["chairman"] == {"id": OPUS, "display_name": "Claude Opus 5.5", "kind": "cloud",
                                    "provider_id": "anthropic", "node_id": None, "upstream_model": "claude-opus-5-5"}
    usage = _persisted(store)["usage"]
    opus_calls = len([c for c in chat_calls(mock) if c["model"] == OPUS])  # stage1, stage2, stage3, title
    assert usage["cost_usd"] == pytest.approx(0.01 * opus_calls)
    assert usage["by_stage"] == pytest.approx({"1": 0.01, "2": 0.01, "3": 0.01, "title": 0.01})
    assert usage["prompt_tokens"] == 10 * len(chat_calls(mock))
    assert log.one("run_summary")["costUsd"] == usage["cost_usd"]
    assert result.to_response().keys() == {"stage1", "stage2", "stage3", "metadata", "failures", "usage"}


async def test_warmup_only_for_a_local_chairman(store):
    mock = MeseMock()
    for model in (QWEN, GPT_OSS, OPUS):
        route_member(mock, model)
    result, _ = await _run(mock, store, {"members": [QWEN, OPUS], "chairman": GPT_OSS}, first=False)
    warmups = [json.loads(r.content) for r in mock.requests if r.url.path == "/v1/mese/warmup"]
    assert warmups == [{"models": [GPT_OSS], "run_id": result.run_id}]
    mock.requests.clear()
    await _run(mock, store, {"members": [QWEN, OPUS], "chairman": OPUS}, first=False)
    assert not [r for r in mock.requests if r.url.path == "/v1/mese/warmup"]


def _hang_all(mock: MeseMock, models):
    for model in models:
        partial = build_mese_sse(model, "partial text").split(b'"finish_reason":"stop"')[0]
        mock.chat_sse(model, partial.rsplit(b"\n\n", 1)[0] + b"\n\n", hang=True)


async def _wait_for_streams(mock: MeseMock, count: int) -> None:
    for _ in range(400):
        if len(mock.streams) >= count and all(s.yielded for s in mock.streams):
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"only {len(mock.streams)} streams started")


async def test_cancelling_a_run_closes_every_in_flight_mese_stream(store):
    mock = MeseMock()
    members = [OPUS, QWEN, GPT]
    _hang_all(mock, members)
    deps = make_deps(mock, store)
    task = asyncio.create_task(run_council(QUERY, {"members": members, "chairman": OPUS}, "conv-1", EventLog(),
                                           deps=deps, is_first_message=True))
    await _wait_for_streams(mock, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(mock.closed_streams()) == 3
    user, assistant = store.get("conv-1")["messages"]  # never an orphan user message
    assert (assistant["status"], assistant["stage1"], assistant["stage3"]["error"]["code"]) == ("cancelled", [], "cancelled")
    assert [(f["stage"], f["model"], f["code"]) for f in assistant["failures"]] == [("1", m, "cancelled") for m in members]
    await deps.client.aclose()


async def test_closing_the_sse_generator_cancels_the_run(store):
    mock = MeseMock()
    members = [QWEN, GPT_OSS]
    _hang_all(mock, members)
    deps = make_deps(mock, store)
    stream = council_event_stream(QUERY, {"members": members, "chairman": GPT_OSS}, "conv-1", deps,
                                  is_first_message=False)
    seen = []
    async for frame in stream:
        seen.extend(parse_sse(frame))
        if len([e for e in seen if e["type"] == "member_streaming"]) == 2:
            break
    await stream.aclose()  # what Starlette does when the client disconnects
    assert len(mock.closed_streams()) == 2 and all(s.closed for s in mock.streams)
    assert seen[0]["type"] == "council_started"
    await deps.client.aclose()


async def test_sse_stream_sends_keepalives_and_ends_with_complete(store):
    mock = MeseMock()
    for model in (OPUS, GPT):
        mock.route_chat(model, lambda req, m=model: _slow(m))
    deps = make_deps(mock, store)
    frames = [f async for f in council_event_stream(QUERY, {"members": [OPUS, GPT], "chairman": OPUS}, "conv-1", deps,
                                                    is_first_message=False, keepalive_s=0.01)]
    await deps.client.aclose()
    assert ": keepalive\n\n" in frames
    events = parse_sse("".join(frames))
    assert events[-1] == {"type": "complete"} and events[-2]["type"] == "run_summary"


def _slow(model):
    chunks = split_frames(build_mese_sse(model, "slow answer\n\nFINAL RANKING:\n1. Response A\n2. Response B"))
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=ReplayStream(chunks, delay=0.01))


async def test_unexpected_runner_error_becomes_an_error_event(store, monkeypatch):
    mock = MeseMock()
    for model in (OPUS, GPT):
        route_member(mock, model)
    deps = make_deps(mock, store)

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "add_assistant_message", broken)
    frames = [f async for f in council_event_stream(QUERY, {"members": [OPUS, GPT], "chairman": OPUS}, "conv-1", deps,
                                                    is_first_message=False)]
    await deps.client.aclose()
    events = parse_sse("".join(frames))
    assert events[-1] == {"type": "error", "message": "OSError: disk full"}
    assert "complete" not in [e["type"] for e in events]


async def test_emitted_event_shapes_match_the_frontend_fixtures(tmp_path, frontend_event_fixtures):
    """Every emitted event has the keys the WP0 frontend fixtures use (§I.6 is the contract with the UI)."""
    fixture_keys = {}
    for fixture in frontend_event_fixtures.values():
        for event in fixture["events"]:
            fixture_keys.setdefault(event["type"], []).append(set(event))
    emitted = EventLog()
    chair_fails = {"content": "x", "finish_reason": "error", "error": {"code": "upstream_timeout", "message": "slow"}}
    ranker_fails = {"content": "", "finish_reason": "error", "error": {"code": "upstream_error", "message": "cut"}}
    scenarios = [
        # queued/loading/thinking, a pre-stream failure, a context-skipped ranker, a failing ranker, chairman failure
        ({"members": [QWEN, GROK, VLLM, OPUS, GPT], "chairman": OPUS}, True, {"stage3": chair_fails}),
        # stage2_skipped, then a successful chairman (final_status/final_streaming/final_ready)
        ({"members": [OPUS, GROK], "chairman": OPUS}, False, {}),
    ]
    for index, (council, first, opus_overrides) in enumerate(scenarios):
        store = ConversationStore(tmp_path / f"s{index}")
        store.create("conv-1")
        mock = MeseMock()
        mock.chat_sse(QWEN, "events.sse")
        route_http_error(mock, GROK, 503, "circuit_open", "open", True)
        patch_model(mock, VLLM, context_length=CONTEXT_RESERVE_TOKENS + 10)
        route_member(mock, VLLM)
        route_member(mock, GPT, stage2=ranker_fails)
        route_member(mock, OPUS, reasoning="hmm", **opus_overrides)
        await _run(mock, store, council, first=first, log=emitted)
    assert set(emitted.types) == set(fixture_keys)
    for event in emitted.events:
        variants = fixture_keys[event["type"]]
        assert set(event) <= set.union(*variants), event
        assert set(event) >= set.intersection(*variants), event

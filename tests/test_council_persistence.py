"""When a council turn is persisted (§I.4 steps 7-8, §I.7): before the title, and also when the run stops early.

- A finished turn is stored before the best-effort title call, so a disconnect during the title keeps it.
- A cancelled run stores ``status: "cancelled"`` and a crashed run ``status: "error"``, with the results
  that had finished, ``stage3.error`` and a ``cancelled`` failure per Mese call still in flight.
- The chairman's cost is stored in ``stage3.cost_usd``; an empty answer is a member failure.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.council import run_council
from backend.council import runner as runner_module
from backend.main import council_event_stream
from backend.storage import ConversationStore
from conftest import MeseMock, ReplayStream, build_mese_sse, split_frames
from test_support import GPT, OPUS, QWEN, EventLog, chat_calls, make_deps, parse_sse, route_member, stage_responder

QUERY = "SQLite or PostgreSQL for a single-user app?"
CANCELLED = "The run was cancelled before it finished (the client disconnected)."


@pytest.fixture
def store(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    store.create("conv-1")
    store.add_user_message("conv-1", QUERY)
    return store


def _hang_on(mock: MeseMock, model: str, stage: str, **contents) -> None:
    """``model`` answers normally, except that its ``stage`` call streams a little text and then hangs."""
    normal = stage_responder(model, {"stage2": "FINAL RANKING:\n1. Response A\n2. Response B", **contents}, cost_usd=0.01)
    partial = build_mese_sse(model, "partial text").split(b'"finish_reason":"stop"')[0].rsplit(b"\n\n", 1)[0] + b"\n\n"

    def respond(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["mese"]["stage"] != stage:
            return normal(request)
        stream = ReplayStream(split_frames(partial), hang=True)
        mock.streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    mock.route_chat(model, respond)


async def _until(predicate, what: str) -> None:
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}")


def _messages(store):
    return store.get("conv-1")["messages"]


# ── COUNCIL-1: persisted before the title ────────────────────────────────────


async def test_disconnect_during_the_title_keeps_the_finished_turn(store):
    mock = MeseMock()
    _hang_on(mock, OPUS, "title")
    route_member(mock, GPT, cost_usd=0.01)
    deps = make_deps(mock, store)
    stream = council_event_stream(QUERY, {"members": [OPUS, GPT], "chairman": OPUS}, "conv-1", deps, is_first_message=True)
    seen = []
    async for frame in stream:
        seen.extend(parse_sse(frame))
        if seen and seen[-1]["type"] == "stage3_complete":
            break
    await _until(lambda: mock.streams and mock.streams[-1].yielded, "the title call")
    await stream.aclose()  # the client reloads the page while the title is generated
    await deps.client.aclose()

    assert [c["stage"] for c in chat_calls(mock)] == ["stage1", "stage1", "stage2", "stage2", "stage3", "title"]
    assert mock.streams[-1].closed  # the title call was cancelled
    user, assistant = _messages(store)
    assert user["role"] == "user" and assistant["status"] == "complete" and assistant["failures"] == []
    assert assistant["stage3"]["response"] == f"{OPUS} stage3 answer" and assistant["stage3"]["error"] is None
    assert [e["model"] for e in assistant["stage1"]] == [OPUS, GPT] and len(assistant["stage2"]) == 2
    assert store.get("conv-1")["title"] == "New Conversation"  # retried on the next turn (see test_api)


async def test_title_cost_and_failure_are_written_onto_the_stored_turn(store):
    mock = MeseMock()
    route_member(mock, OPUS, cost_usd=0.01, title={"content": "", "finish_reason": "error",
                                                   "error": {"code": "upstream_timeout", "message": "slow"}})
    route_member(mock, GPT, cost_usd=0.01)
    log = EventLog()
    deps = make_deps(mock, store)
    result = await run_council(QUERY, {"members": [OPUS, GPT], "chairman": OPUS}, "conv-1", log, deps=deps,
                               is_first_message=True)
    await deps.client.aclose()
    stored = _messages(store)[-1]
    failure = {"stage": "title", "model": OPUS, "code": "upstream_timeout", "message": "slow"}
    assert stored["failures"] == [failure] and stored["status"] == "partial" == result.status
    assert stored["usage"]["by_stage"]["title"] == pytest.approx(0.01) and stored["usage"] == result.usage
    assert log.one("run_summary")["status"] == "partial" and log.one("title_complete")["data"]["title"] == "New Conversation"
    assert log.index("stage3_complete") < log.index("title_complete") < log.index("run_summary") < log.index("complete")


# ── COUNCIL-4: cancelled and crashed runs are persisted too ──────────────────


async def test_cancel_in_stage1_stores_a_cancelled_turn_with_the_finished_members(store):
    mock = MeseMock()
    route_member(mock, OPUS, cost_usd=0.02)
    _hang_on(mock, QWEN, "stage1")
    _hang_on(mock, GPT, "stage1")
    log = EventLog()
    deps = make_deps(mock, store)
    task = asyncio.create_task(run_council(QUERY, {"members": [OPUS, QWEN, GPT], "chairman": OPUS}, "conv-1", log,
                                           deps=deps, is_first_message=True))
    await _until(lambda: log.of("member_complete") and len(mock.streams) == 2 and all(s.yielded for s in mock.streams),
                 "one finished member and two hanging ones")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await deps.client.aclose()

    assert all(s.closed for s in mock.streams)
    user, assistant = _messages(store)
    assert user["content"] == QUERY and assistant["status"] == "cancelled"
    assert [e["model"] for e in assistant["stage1"]] == [OPUS] and assistant["stage1"][0]["cost_usd"] == 0.02
    assert assistant["stage2"] == [] and assistant["metadata"] == {"label_to_model": {}, "aggregate_rankings": []}
    assert assistant["failures"] == [{"stage": "1", "model": model, "code": "cancelled",
                                      "message": "Cancelled before this call finished."} for model in (QWEN, GPT)]
    assert assistant["stage3"] == {"model": OPUS, "response": "", "cost_usd": None,
                                   "error": {"code": "cancelled", "message": CANCELLED}}
    assert assistant["usage"]["by_stage"]["1"] == pytest.approx(0.02)
    assert assistant["council_snapshot"]["run_id"].startswith("run_")
    assert "run_summary" not in log.types and store.get("conv-1")["title"] == "New Conversation"


async def test_cancel_in_stage2_keeps_stage1_and_the_finished_rankings(store):
    mock = MeseMock()
    route_member(mock, OPUS, stage2="FINAL RANKING:\n1. Response B\n2. Response A")
    _hang_on(mock, QWEN, "stage2")
    log = EventLog()
    deps = make_deps(mock, store)
    task = asyncio.create_task(run_council(QUERY, {"members": [OPUS, QWEN], "chairman": OPUS}, "conv-1", log, deps=deps))
    await _until(lambda: log.of("stage2_model_complete") and mock.streams and mock.streams[0].yielded, "Stage 2")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await deps.client.aclose()

    assistant = _messages(store)[-1]
    assert assistant["status"] == "cancelled" and [e["model"] for e in assistant["stage1"]] == [OPUS, QWEN]
    assert [e["model"] for e in assistant["stage2"]] == [OPUS]
    assert assistant["stage2"][0]["parsed_ranking"] == ["Response B", "Response A"]
    assert assistant["metadata"]["label_to_model"] == {"Response A": OPUS, "Response B": QWEN}
    assert assistant["metadata"]["aggregate_rankings"][0]["model"] == QWEN
    assert assistant["failures"] == [{"stage": "2", "model": QWEN, "code": "cancelled",
                                      "message": "Cancelled before this call finished."}]
    assert assistant["stage3"]["error"]["code"] == "cancelled"


async def test_an_unexpected_error_stores_an_error_turn_and_reaches_the_client(store, monkeypatch):
    mock = MeseMock()
    for model in (OPUS, GPT):
        route_member(mock, model)

    def broken(*_args, **_kwargs):
        raise RuntimeError("ranking prompt exploded")

    monkeypatch.setattr(runner_module, "build_ranking", broken)
    deps = make_deps(mock, store)
    frames = [f async for f in council_event_stream(QUERY, {"members": [OPUS, GPT], "chairman": OPUS}, "conv-1", deps,
                                                    is_first_message=False)]
    await deps.client.aclose()
    assert parse_sse("".join(frames))[-1] == {"type": "error", "message": "RuntimeError: ranking prompt exploded"}
    assistant = _messages(store)[-1]
    assert assistant["status"] == "error" and [e["model"] for e in assistant["stage1"]] == [OPUS, GPT]
    assert assistant["stage3"]["error"] == {"code": "run_failed",
                                           "message": "The council run failed: RuntimeError: ranking prompt exploded"}
    assert assistant["failures"] == []


async def test_a_failing_store_still_reports_the_original_error(store, monkeypatch):
    mock = MeseMock()
    for model in (OPUS, GPT):
        route_member(mock, model)
    deps = make_deps(mock, store)
    monkeypatch.setattr(store, "add_assistant_message", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        await run_council(QUERY, {"members": [OPUS, GPT], "chairman": OPUS}, "conv-1", deps=deps)
    await deps.client.aclose()
    assert len(_messages(store)) == 1


# ── COUNCIL-7 / COUNCIL-8 ────────────────────────────────────────────────────


async def test_empty_answer_is_a_member_failure_and_is_not_ranked(store):
    mock = MeseMock()
    route_member(mock, OPUS, cost_usd=0.03)
    route_member(mock, GPT)
    route_member(mock, QWEN, stage1={"content": "", "finish_reason": "length", "reasoning": "thinking…"})
    log = EventLog()
    deps = make_deps(mock, store)
    result = await run_council(QUERY, {"members": [OPUS, GPT, QWEN], "chairman": OPUS}, "conv-1", log, deps=deps)
    await deps.client.aclose()
    assert log.one("member_error", memberId=QWEN)["code"] == "empty_response"
    assert QWEN not in result.metadata["label_to_model"].values() and [e["model"] for e in result.stage1] == [OPUS, GPT]
    assert result.failures[0]["code"] == "empty_response" and result.status == "partial"
    assert result.stage3["cost_usd"] == 0.03 and log.one("stage3_complete")["data"]["cost_usd"] == 0.03
    assert _messages(store)[-1]["stage3"] == {"model": OPUS, "response": f"{OPUS} synthesis", "latency_ms": result.stage3["latency_ms"],
                                              "cost_usd": 0.03, "error": None}

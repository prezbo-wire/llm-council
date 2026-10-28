"""MeseClient: SSE parsing and the §I.2 outcome → MemberResult mapping (mock Mese only)."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import httpx
import pytest

from backend.contracts import MemberResult
from backend.mese_client import MeseClient, MeseError, iter_sse, status_from_frame
from conftest import TEST_MESE_TOKEN, TEST_MESE_URL, MeseMock, build_mese_sse, load_fixture_json
from test_support import make_client

EXPECTED = load_fixture_json("expected.json")["cases"]
SSE_CASES = sorted(name for name in EXPECTED if name.endswith(".sse"))
HTTP_CASES = sorted(name for name in EXPECTED if name.endswith(".json"))
MESSAGES = [{"role": "user", "content": "What is 2+2?"}]


async def _stream(client: MeseClient, model: str, **kwargs: Any):
    deltas: List[str] = []
    statuses: List[Dict[str, Any]] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    async def on_status(status: Dict[str, Any]) -> None:
        statuses.append(status)

    result = await client.stream_chat(model, MESSAGES, stage=kwargs.get("stage", "stage1"), run_id="run_test",
                                      conversation_id="conv-1", on_delta=on_delta, on_status=on_status)
    return result, deltas, statuses


def _assert_matches(result: MemberResult, deltas: List[str], statuses: List[Dict[str, Any]], expected: Dict[str, Any]):
    assert isinstance(result, MemberResult)
    for key, value in expected.items():
        if key == "deltas":
            assert deltas == value
        elif key == "statuses":
            assert statuses == value
        else:
            assert getattr(result, key) == value, key


@pytest.mark.parametrize("chunk_size", [None, 7, 1])
@pytest.mark.parametrize("fixture", SSE_CASES)
async def test_golden_transcripts_map_to_expected_member_results(fixture, chunk_size):
    expected = EXPECTED[fixture]
    mock = MeseMock()
    mock.chat_sse(expected["model"], fixture, chunk_size=chunk_size)
    client = make_client(mock)
    try:
        result, deltas, statuses = await _stream(client, expected["model"])
    finally:
        await client.aclose()
    _assert_matches(result, deltas, statuses, expected)
    assert mock.streams and all(s.closed for s in mock.streams)


@pytest.mark.parametrize("fixture", HTTP_CASES)
async def test_pre_stream_http_errors_use_the_error_body(fixture):
    expected = EXPECTED[fixture]
    mock = MeseMock()
    mock.chat_http_error(expected["model"], fixture)
    client = make_client(mock)
    result, deltas, statuses = await _stream(client, expected["model"])
    await client.aclose()
    _assert_matches(result, deltas, statuses, expected)


async def test_connect_failure_is_mese_unreachable_and_never_none():
    expected = EXPECTED["<connect error>"]
    mock = MeseMock()
    mock.unreachable = True
    client = make_client(mock)
    result, deltas, statuses = await _stream(client, "spark/qwen3.8:27b")
    await client.aclose()
    assert result is not None
    assert (result.ok, result.error_code, result.retryable, result.content, result.cost_usd) == \
        (expected["ok"], expected["error_code"], expected["retryable"], expected["content"], expected["cost_usd"])
    assert result.error_message and TEST_MESE_TOKEN not in result.error_message
    assert deltas == [] and statuses == []


async def test_request_carries_the_mese_extension_and_no_sampling_params():
    mock = MeseMock()
    mock.chat_sse("anthropic/claude-sonnet-5-5", "plain.sse")
    client = make_client(mock)
    await client.stream_chat("anthropic/claude-sonnet-5-5", MESSAGES, stage="stage2", run_id="run_abc",
                             conversation_id="conv-9")
    await client.aclose()
    (request,) = [r for r in mock.requests if r.url.path == "/v1/chat/completions"]
    body = json.loads(request.content)
    assert body == {"model": "anthropic/claude-sonnet-5-5", "messages": MESSAGES, "stream": True,
                    "mese": {"client": "llm-council", "run_id": "run_abc", "conversation_id": "conv-9",
                             "stage": "stage2", "events": True}}
    assert request.headers["authorization"] == f"Bearer {TEST_MESE_TOKEN}"
    assert str(request.url).startswith(TEST_MESE_URL)


async def test_keepalives_and_comments_are_ignored_and_reasoning_is_not_kept():
    raw = (b": mese req_1\n\n: keepalive\n\n" + build_mese_sse("spark/qwen3.8:27b", "Answer.", reasoning="secret plan")
           .replace(b"data: [DONE]", b": keepalive\n\ndata: [DONE]"))
    mock = MeseMock()
    mock.chat_sse("spark/qwen3.8:27b", raw)
    client = make_client(mock)
    result, deltas, statuses = await _stream(client, "spark/qwen3.8:27b")
    await client.aclose()
    assert result.ok and result.content == "Answer." and "secret" not in result.content
    assert statuses == [{"state": "started", "attempt": 1}, {"state": "thinking"}]
    assert deltas == ["Answer."]


async def test_finish_reason_length_is_ok_and_error_chunk_maps_code():
    mock = MeseMock()
    mock.chat_text("a/len", "cut", finish_reason="length")
    mock.chat_text("a/err", "par", finish_reason="error", error={"code": "load_failed", "message": "boom", "retryable": True})
    client = make_client(mock)
    ok, _, _ = await _stream(client, "a/len")
    err, _, _ = await _stream(client, "a/err")
    await client.aclose()
    assert (ok.ok, ok.finish_reason, ok.content) == (True, "length", "cut")
    assert (err.ok, err.error_code, err.error_message, err.retryable, err.content) == (False, "load_failed", "boom", True, "par")


@pytest.mark.parametrize("content,finish_reason,reasoning", [
    ("", "length", "spent the whole budget thinking"),  # reasoning only, then the token budget ran out
    ("", "stop", None),
    ("  \n ", "stop", None),
])
async def test_stop_or_length_without_answer_text_is_empty_response(content, finish_reason, reasoning):
    mock = MeseMock()
    mock.chat_text("spark/qwen3.8:27b", content, finish_reason=finish_reason, reasoning=reasoning)
    client = make_client(mock)
    result, deltas, _ = await _stream(client, "spark/qwen3.8:27b")
    await client.aclose()
    assert (result.ok, result.error_code, result.retryable, result.finish_reason) == (False, "empty_response", False, finish_reason)
    assert f"finish_reason={finish_reason}" in result.error_message
    assert result.cost_usd == 0.0 and result.usage is not None  # the call may still have cost something


async def test_malformed_data_line_is_an_upstream_error():
    raw = b": mese req\n\ndata: {\"id\": \"x\", \"choices\": [{\"index\":0,\"delta\":{\"content\":\"Hi\"}}]}\n\ndata: {not json\n\n"
    mock = MeseMock()
    mock.chat_sse("a/b", raw)
    client = make_client(mock)
    result, deltas, _ = await _stream(client, "a/b")
    await client.aclose()
    assert (result.ok, result.error_code, result.content, deltas) == (False, "upstream_error", "Hi", ["Hi"])
    assert "malformed" in result.error_message


async def test_http_error_without_a_mese_body_falls_back_by_status():
    mock = MeseMock()
    mock.route_chat("a/b", lambda _r: httpx.Response(502, text="<html>bad gateway</html>"))
    client = make_client(mock)
    result, _, _ = await _stream(client, "a/b")
    await client.aclose()
    assert (result.ok, result.error_code, result.retryable) == (False, "upstream_error", True)
    assert "502" in result.error_message


async def test_wrong_token_is_unauthorized_and_token_is_never_echoed():
    mock = MeseMock()
    mock.chat_sse("anthropic/claude-sonnet-5-5", "plain.sse")
    client = make_client(mock, token="mese_ck_wrong")
    result, _, _ = await _stream(client, "anthropic/claude-sonnet-5-5")
    await client.aclose()
    assert (result.ok, result.error_code, result.retryable) == (False, "unauthorized", False)


class _BrokenStream(httpx.AsyncByteStream):
    def __init__(self, first: bytes) -> None:
        self.first = first
        self.closed = False

    async def __aiter__(self):
        yield self.first
        raise httpx.ReadError("connection reset")

    async def aclose(self) -> None:
        self.closed = True


async def test_connection_lost_mid_stream_keeps_partial_text():
    first = build_mese_sse("a/b", "Partial").split(b"data: {\"id\"", 1)
    head = first[0] + b"data: {\"id\"" + first[1].split(b"\n\n", 1)[0] + b"\n\n"
    stream = _BrokenStream(head)
    mock = MeseMock()
    mock.route_chat("a/b", lambda _r: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))
    client = make_client(mock)
    result, deltas, _ = await _stream(client, "a/b")
    await client.aclose()
    assert (result.ok, result.error_code, result.retryable, result.content) == (False, "upstream_error", True, "Partial")
    assert "connection to Mese lost" in result.error_message and stream.closed


async def test_cancelling_a_stream_closes_it():
    mock = MeseMock()
    partial = build_mese_sse("spark/qwen3.8:27b", "Thinking about it").split(b'"finish_reason":"stop"')[0]
    mock.chat_sse("spark/qwen3.8:27b", partial.rsplit(b"\n\n", 1)[0] + b"\n\n", hang=True)
    client = make_client(mock)
    task = asyncio.create_task(client.stream_chat("spark/qwen3.8:27b", MESSAGES, stage="stage1", run_id="r",
                                                  conversation_id="c"))
    for _ in range(100):
        if mock.streams and mock.streams[0].yielded:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await client.aclose()
    assert mock.streams[0].closed


async def test_iter_sse_dispatches_named_and_multiline_events():
    async def lines():
        for line in [": c", "event: mese", 'data: {"type":"loading","node_id":"n"}', "", "data: a", "data: b", "",
                     "id: 7", "", "data: trailing-without-blank"]:
            yield line

    events = [e async for e in iter_sse(lines())]
    assert events == [("mese", '{"type":"loading","node_id":"n"}'), (None, "a\nb")]


def test_status_from_frame_mapping_and_unknown_frames():
    assert status_from_frame({"type": "queued", "node_id": "spark", "host_id": "spark", "position": 1, "reason": "slots"}) == \
        {"state": "queued", "position": 1, "node_id": "spark", "reason": "slots"}
    assert status_from_frame({"type": "retrying", "attempt": 2, "reason": "upstream_error", "delay_ms": 5}) == \
        {"state": "retrying", "attempt": 2, "reason": "upstream_error"}
    assert status_from_frame({"type": "future_frame"}) is None


# ── non-streaming calls ──────────────────────────────────────────────────────


async def test_list_models_is_cached_for_ten_seconds():
    now = [100.0]
    mock = MeseMock()
    client = MeseClient(TEST_MESE_URL, TEST_MESE_TOKEN, transport=mock.transport, clock=lambda: now[0])
    first = await client.list_models()
    await client.list_models()
    assert len([r for r in mock.requests if r.url.path == "/v1/models"]) == 1
    now[0] += 10.5
    await client.list_models()
    await client.list_models(force=True)
    await client.aclose()
    assert len([r for r in mock.requests if r.url.path == "/v1/models"]) == 3
    assert [m["id"] for m in first] == [m["id"] for m in mock.models["data"]]


async def test_list_models_and_plan_raise_typed_errors():
    mock = MeseMock()
    mock.unreachable = True
    client = make_client(mock)
    with pytest.raises(MeseError) as down:
        await client.list_models()
    assert down.value.unreachable and down.value.code == "mese_unreachable"
    mock.unreachable = False
    bad = make_client(mock, token="mese_ck_bad")
    with pytest.raises(MeseError) as unauthorized:
        await bad.plan(["a/b"])
    assert (unauthorized.value.code, unauthorized.value.status, unauthorized.value.unreachable) == ("unauthorized", 401, False)
    await client.aclose()
    await bad.aclose()


async def test_plan_warmup_and_status():
    mock = MeseMock()
    client = make_client(mock)
    assert await client.plan(["spark/qwen3.8:27b"]) == mock.plan
    assert await client.warmup(["spark/gpt-oss:120b"], "run_x") is None
    assert await client.status() == mock.status
    warmup = [json.loads(r.content) for r in mock.requests if r.url.path == "/v1/mese/warmup"]
    assert warmup == [{"models": ["spark/gpt-oss:120b"], "run_id": "run_x"}]
    mock.unreachable = True
    assert await client.warmup(["spark/gpt-oss:120b"], "run_x") is None  # swallowed
    down = await client.status()
    assert down["code"] == "mese_unreachable" and "error" in down
    await client.aclose()

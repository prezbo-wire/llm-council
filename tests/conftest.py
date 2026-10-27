"""Shared pytest fixtures for llm-council (owned by WP0; read-only for WP7).

Mese is always mocked. ``MeseMock`` is an ``httpx.MockTransport`` factory that replays the golden
Mese transcripts in ``tests/fixtures/mese/*.sse`` (copied from the Mese repo) and records every
request and every stream close, so cancellation can be asserted. Real network access is blocked
for the whole session: only loopback and Unix sockets may connect.

Typical use (WP7)::

    async def test_member(mese_mock):
        mese_mock.chat_sse("anthropic/claude-sonnet-5-5", "plain.sse")
        client = MeseClient(TEST_MESE_URL, TEST_MESE_TOKEN, transport=mese_mock.transport)
        result = await client.stream_chat("anthropic/claude-sonnet-5-5", [...], stage="stage1", run_id="r", conversation_id="c")
        assert result.ok and mese_mock.chat_bodies()[0]["mese"]["events"] is True
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Union

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MESE_FIXTURES = FIXTURES / "mese"
CONVERSATION_FIXTURES = FIXTURES / "conversations"
FRONTEND_FIXTURES = Path(__file__).parent.parent / "frontend" / "src" / "dev" / "fixtures"

CLOUD_PROVIDER_IDS = {"anthropic", "openai", "google", "xai", "deepseek", "moonshot", "zai", "minimax", "mistral", "cohere"}
TEST_MESE_URL = "http://127.0.0.1:8790"
TEST_MESE_TOKEN = "mese_ck_" + "test-token-not-a-real-key".ljust(43, "x")

# Set before any backend module reads its configuration. Never a real key.
os.environ["MESE_URL"] = TEST_MESE_URL
os.environ["MESE_API_KEY"] = TEST_MESE_TOKEN
os.environ.pop("OPENROUTER_API_KEY", None)

# ── network guard ───────────────────────────────────────────────────────────

_LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", ""}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


class BlockedNetworkError(OSError):
    """Raised when a test tries to reach a non-loopback host."""


def _is_loopback(host: Any) -> bool:
    if not isinstance(host, str):
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def _check_address(sock: socket.socket, address: Any) -> None:
    if sock.family == getattr(socket, "AF_UNIX", object()):
        return
    host = address[0] if isinstance(address, tuple) and address else address
    if not _is_loopback(host):
        raise BlockedNetworkError(f"[test network guard] blocked connection to {host!r}")


def _guarded_connect(self: socket.socket, address: Any) -> None:
    _check_address(self, address)
    return _real_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
    _check_address(self, address)
    return _real_connect_ex(self, address)


def _guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any):
    if host is not None and not _is_loopback(host if isinstance(host, str) else str(host)):
        raise BlockedNetworkError(f"[test network guard] blocked DNS lookup for {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


def pytest_configure(config: pytest.Config) -> None:
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]


def pytest_unconfigure(config: pytest.Config) -> None:
    socket.socket.connect = _real_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _real_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _real_getaddrinfo  # type: ignore[assignment]


# ── fixture loading ─────────────────────────────────────────────────────────


def load_fixture_bytes(name: str) -> bytes:
    return (MESE_FIXTURES / name).read_bytes()


def load_fixture_json(name: str) -> Any:
    return json.loads((MESE_FIXTURES / name).read_text())


def load_conversation(name: str) -> Dict[str, Any]:
    return json.loads((CONVERSATION_FIXTURES / name).read_text())


def split_frames(data: bytes) -> List[bytes]:
    """Splits SSE bytes into one chunk per frame (each keeps its trailing blank line)."""
    parts = data.split(b"\n\n")
    out = [p + b"\n\n" for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def split_every(data: bytes, size: int) -> List[bytes]:
    """Splits bytes into fixed-size chunks (exercises partial lines in the client parser)."""
    return [data[i : i + size] for i in range(0, len(data), size)]


def build_mese_sse(
    model: str,
    content: str,
    *,
    request_id: str = "req_0192f1c0-a4b0-7c3e-8d2a-5b6c7d8e9fff",
    reasoning: Optional[str] = None,
    finish_reason: str = "stop",
    error: Optional[Dict[str, Any]] = None,
    cost_usd: Optional[float] = 0.0,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    events: bool = True,
    warnings: Optional[List[str]] = None,
) -> bytes:
    """Synthesizes a Mese stream for any model, following the golden format (Mese manifest meseOutputRules).

    ``error`` (for finish_reason "error") is a V1ErrorObject-like dict: {code, message, retryable?}.
    """
    prefix, upstream = model.split("/", 1)
    kind = "cloud" if prefix in CLOUD_PROVIDER_IDS else ("custom" if prefix == "ft" else "local")
    node = None if kind == "cloud" else ("spark" if kind == "custom" else prefix)
    resolved = {"model": model, "kind": kind, "provider_id": prefix if kind == "cloud" else None, "node_id": node,
                "host_id": node, "runtime": None if kind == "cloud" else "ollama", "upstream_model": upstream}
    base = {"id": f"chatcmpl-{request_id}", "object": "chat.completion.chunk", "created": 1793000000, "model": model}
    dump = lambda o: json.dumps(o, separators=(",", ":"), ensure_ascii=False)
    out = [f": mese {request_id}\n\n"]
    if events:
        out.append(f"event: mese\ndata: {dump({'type': 'started', 'attempt': 1, 'resolved': resolved})}\n\n")
    role_sent = False
    for key, text in (("reasoning_content", reasoning), ("content", content)):
        if text:
            delta = {} if role_sent else {"role": "assistant"}
            delta[key] = text
            role_sent = True
            out.append(f"data: {dump({**base, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})}\n\n")
    usage: Dict[str, Any] = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                             "total_tokens": prompt_tokens + completion_tokens, "prompt_tokens_details": {"cached_tokens": 0},
                             "completion_tokens_details": {"reasoning_tokens": 0}}
    if cost_usd is not None:
        usage["cost"] = cost_usd
    finish: Dict[str, Any] = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}
    status = "ok"
    if finish_reason == "error":
        err = error or {"code": "upstream_error", "message": "upstream error"}
        finish["error"] = {"message": err["message"], "type": "upstream_error", "code": err["code"], "param": None,
                           "mese": {"request_id": request_id, "retryable": err.get("retryable", True)}}
        status = "error"
    finish["usage"] = usage
    finish["mese"] = {"request_id": request_id, "status": status, "resolved": resolved, "cost_usd": cost_usd,
                      "cost_source": "unknown" if cost_usd is None else ("local" if kind == "local" else "computed"),
                      "usage_source": "upstream", "queue_ms": 0, "load_ms": 0, "ttft_ms": 100, "latency_ms": 1000, "attempts": 1,
                      "dropped_params": [], "warnings": warnings or []}
    out.append(f"data: {dump(finish)}\n\n")
    out.append("data: [DONE]\n\n")
    return "".join(out).encode()


# ── MockTransport replay ────────────────────────────────────────────────────


class ReplayStream(httpx.AsyncByteStream):
    """Async byte stream that yields chunks (optionally with delays or a final hang) and records closes."""

    def __init__(self, chunks: List[bytes], *, delay: float = 0.0, hang: bool = False) -> None:
        self.chunks = chunks
        self.delay = delay
        self.hang = hang
        self.closed = False
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.yielded += 1
            yield chunk
        if self.hang:
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


Responder = Callable[[httpx.Request], Union[httpx.Response, "asyncio.Future[httpx.Response]"]]


class MeseMock:
    """A fake Mese behind ``httpx.MockTransport``.

    - ``GET /v1/models``, ``POST /v1/mese/plan|warmup``, ``GET /v1/mese/status`` answer from the
      ``v1_*.json`` fixtures (override via the attributes of the same name).
    - ``POST /v1/chat/completions`` is routed by request ``model`` to a responder registered with
      ``chat_sse`` / ``chat_text`` / ``chat_http_error`` / ``route_chat``; unknown models get 404.
    - Every request is recorded in ``requests``; every streamed body in ``streams`` (``.closed``).
    - Bearer auth is checked against ``token`` (401 with the OpenAI error body otherwise).
    """

    def __init__(self, token: str = TEST_MESE_TOKEN) -> None:
        self.token = token
        self.requests: List[httpx.Request] = []
        self.streams: List[ReplayStream] = []
        self.unreachable = False
        self.models: Dict[str, Any] = load_fixture_json("v1_models.json")
        self.plan: Dict[str, Any] = load_fixture_json("v1_plan.json")
        self.warmup: Dict[str, Any] = load_fixture_json("v1_warmup.json")
        self.status: Dict[str, Any] = load_fixture_json("v1_status.json")
        self._chat: Dict[str, Responder] = {}
        self.transport = httpx.MockTransport(self.handler)

    # registration ---------------------------------------------------------

    def route_chat(self, model: str, responder: Responder) -> None:
        self._chat[model] = responder

    def chat_sse(self, model: str, data: Union[str, bytes], *, delay: float = 0.0, hang: bool = False,
                 chunk_size: Optional[int] = None) -> None:
        """Replays a fixture file name (e.g. 'events.sse') or raw bytes for ``model``."""
        raw = load_fixture_bytes(data) if isinstance(data, str) else data
        chunks = split_every(raw, chunk_size) if chunk_size else split_frames(raw)

        def respond(_req: httpx.Request) -> httpx.Response:
            stream = ReplayStream(list(chunks), delay=delay, hang=hang)
            self.streams.append(stream)
            return httpx.Response(200, headers={"content-type": "text/event-stream", "cache-control": "no-cache"}, stream=stream)

        self.route_chat(model, respond)

    def chat_text(self, model: str, content: str, **kwargs: Any) -> None:
        """Replays a synthesized stream (see build_mese_sse) for ``model``."""
        delay = kwargs.pop("delay", 0.0)
        hang = kwargs.pop("hang", False)
        self.chat_sse(model, build_mese_sse(model, content, **kwargs), delay=delay, hang=hang)

    def chat_http_error(self, model: str, fixture: str = "pre_stream_error.json") -> None:
        """Answers with a pre-stream HTTP error fixture ({status, headers, body})."""
        f = load_fixture_json(fixture)
        self.route_chat(model, lambda _req: httpx.Response(f["status"], headers=f["headers"], json=f["body"]))

    # inspection -----------------------------------------------------------

    def chat_bodies(self) -> List[Dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/v1/chat/completions"]

    def closed_streams(self) -> List[ReplayStream]:
        return [s for s in self.streams if s.closed]

    # transport --------------------------------------------------------------

    def _v1_error(self, status: int, code: str, message: str) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": message, "type": "invalid_request_error", "code": code, "param": None,
                                                      "mese": {"request_id": "req_mock", "retryable": False}}})

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unreachable:
            raise httpx.ConnectError("connection refused (mocked)", request=request)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return self._v1_error(401, "unauthorized", "invalid or missing Mese token")
        path, method = request.url.path, request.method
        if method == "GET" and path == "/v1/models":
            return httpx.Response(200, json=self.models)
        if method == "GET" and path == "/v1/mese/status":
            return httpx.Response(200, json=self.status)
        if method == "POST" and path == "/v1/mese/plan":
            return httpx.Response(200, json=self.plan)
        if method == "POST" and path == "/v1/mese/warmup":
            return httpx.Response(200, json=self.warmup)
        if method == "POST" and path == "/v1/chat/completions":
            model = json.loads(request.content).get("model", "")
            responder = self._chat.get(model)
            if responder is None:
                return self._v1_error(404, "model_not_found", f"unknown model '{model}'")
            result = responder(request)
            return await result if asyncio.isfuture(result) or asyncio.iscoroutine(result) else result  # type: ignore[return-value]
        return httpx.Response(404, json={"error": {"message": "not found"}})


def make_mese_transport(chat: Optional[Dict[str, str]] = None) -> MeseMock:
    """Factory: a MeseMock whose chat routes replay fixtures, e.g. {'spark/qwen3.8:27b': 'events.sse'}."""
    mock = MeseMock()
    for model, fixture in (chat or {}).items():
        if fixture.endswith(".json"):
            mock.chat_http_error(model, fixture)
        else:
            mock.chat_sse(model, fixture)
    return mock


@pytest.fixture
def mese_mock() -> MeseMock:
    return MeseMock()


@pytest.fixture
def mese_transport(mese_mock: MeseMock) -> httpx.MockTransport:
    return mese_mock.transport


@pytest.fixture
def mese_expected() -> Dict[str, Any]:
    """Normative MemberResult expectations per fixture (tests/fixtures/mese/expected.json)."""
    return load_fixture_json("expected.json")["cases"]


@pytest.fixture
def legacy_conversations() -> Dict[str, Dict[str, Any]]:
    return {p.name: json.loads(p.read_text()) for p in sorted(CONVERSATION_FIXTURES.glob("legacy_*.json"))}


@pytest.fixture
def frontend_event_fixtures() -> Dict[str, Dict[str, Any]]:
    return {p.stem: json.loads(p.read_text()) for p in sorted(FRONTEND_FIXTURES.glob("*.json"))}

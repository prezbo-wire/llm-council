"""Mese client (Mese ARCHITECTURE §I.1–§I.3).

One pooled ``httpx.AsyncClient`` talks to Mese's OpenAI-compatible API. ``stream_chat`` parses the
Mese SSE stream and always returns a :class:`~backend.contracts.MemberResult`; it never returns
``None`` and never raises for upstream or transport failures (§I.2):

=====================================  =================================================
finished with ``stop`` or ``length``   ``ok=True`` when there is answer text
``stop``/``length`` with no text       ``error_code="empty_response"`` (e.g. a reasoning
                                       model spent the whole token budget on reasoning)
``content_filter``                     ``error_code="content_filtered"``
``error``                              ``error_code`` = Mese ``error.code``
HTTP error before the stream           ``error_code`` from the error body
connect failure                        ``error_code="mese_unreachable"``
stream ended without a finish chunk    ``error_code="upstream_error"``
=====================================  =================================================
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

import httpx

from .config import safe_url
from .contracts import MemberResult, OnDelta, OnStatus

log = logging.getLogger(__name__)

CLIENT_NAME = "llm-council"
MODELS_CACHE_TTL_S = 10.0
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=10.0)
MAX_ERROR_MESSAGE = 500

#: Fallback error codes for HTTP errors whose body is not a Mese error body.
_HTTP_FALLBACK_CODES = {401: "unauthorized", 404: "model_not_found", 429: "upstream_rate_limited"}


class MeseError(Exception):
    """A failed non-streaming Mese call (``list_models``, ``plan``).

    ``code`` is ``"mese_unreachable"`` when Mese could not be reached, otherwise the Mese error code.
    """

    def __init__(self, code: str, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    @property
    def unreachable(self) -> bool:
        return self.code == "mese_unreachable"


class _MalformedStream(Exception):
    """A data line that is not valid JSON; Mese never sends one, so the stream is abandoned."""


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_ERROR_MESSAGE else text[: MAX_ERROR_MESSAGE - 1] + "…"


def parse_error_body(raw: bytes, status: int) -> Tuple[str, str, bool]:
    """Returns ``(code, message, retryable)`` from a Mese/OpenAI error body, with HTTP fallbacks."""
    retryable = status >= 500 or status == 429
    try:
        error = json.loads(raw).get("error")
    except (ValueError, AttributeError):
        error = None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        mese = error.get("mese") if isinstance(error.get("mese"), dict) else {}
        message = error.get("message") if isinstance(error.get("message"), str) else error["code"]
        return error["code"], _truncate(message), bool(mese.get("retryable", retryable))
    code = _HTTP_FALLBACK_CODES.get(status, "upstream_error")
    return code, f"Mese returned HTTP {status}", retryable


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[Tuple[Optional[str], str]]:
    """Yields ``(event_name, data)`` per SSE event. Comments (``:`` lines, e.g. keepalives) are skipped.

    An event is dispatched at its blank line; an unterminated trailing event is discarded (SSE spec).
    """
    event: Optional[str] = None
    data: List[str] = []
    async for line in lines:
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)


def status_from_frame(frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Maps a ``MeseFrame`` to an ``on_status`` payload (§I.6 mapping); unknown frames map to None."""
    kind = frame.get("type")
    if kind == "queued":
        status = {"state": "queued", "position": frame.get("position"), "node_id": frame.get("node_id"),
                  "reason": frame.get("reason")}
    elif kind == "loading":
        status = {"state": "loading", "node_id": frame.get("node_id")}
    elif kind == "started":
        status = {"state": "started", "attempt": frame.get("attempt")}
    elif kind == "retrying":
        status = {"state": "retrying", "attempt": frame.get("attempt"), "reason": frame.get("reason")}
    else:
        return None
    return {key: value for key, value in status.items() if value is not None}


def usage_from_wire(usage: Any) -> Optional[Dict[str, int]]:
    """``V1Usage`` → ``{prompt_tokens, completion_tokens, reasoning_tokens}``."""
    if not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details")
    reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else 0
    return {"prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "reasoning_tokens": int(reasoning or 0)}


class _StreamAccumulator:
    """Folds one Mese chat stream into a MemberResult, firing the callbacks as frames arrive."""

    def __init__(self, model: str, on_delta: Optional[OnDelta], on_status: Optional[OnStatus]) -> None:
        self.model = model
        self.on_delta = on_delta
        self.on_status = on_status
        self.parts: List[str] = []
        self.thinking = False
        self.resolved: Optional[Dict[str, Any]] = None
        self.finish: Optional[Dict[str, Any]] = None
        self.usage: Any = None

    async def feed(self, event: Optional[str], data: str) -> bool:
        """Handles one SSE event; returns True at ``[DONE]``."""
        if event == "mese":
            await self._frame(data)
            return False
        if event not in (None, "message"):
            return False
        if data.strip() == "[DONE]":
            return True
        try:
            chunk = json.loads(data)
        except ValueError:
            raise _MalformedStream("malformed data line in Mese stream") from None
        if isinstance(chunk, dict):
            await self._chunk(chunk)
        return False

    async def _frame(self, data: str) -> None:
        try:
            frame = json.loads(data)
        except ValueError:
            log.warning("ignoring malformed Mese status frame for %s", self.model)
            return
        if not isinstance(frame, dict):
            return
        if frame.get("type") == "started" and isinstance(frame.get("resolved"), dict):
            self.resolved = frame["resolved"]
        status = status_from_frame(frame)
        if status is not None and self.on_status is not None:
            await self.on_status(status)

    async def _chunk(self, chunk: Dict[str, Any]) -> None:
        choices = chunk.get("choices")
        if not choices:  # the optional include_usage chunk
            if self.usage is None:
                self.usage = chunk.get("usage")
            return
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        if delta.get("reasoning_content") and not self.thinking:
            self.thinking = True
            if self.on_status is not None:
                await self.on_status({"state": "thinking"})
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.parts.append(content)
            if self.on_delta is not None:
                await self.on_delta(content)
        if choice.get("finish_reason") and self.finish is None:
            self.finish = chunk
            self.usage = chunk.get("usage", self.usage)

    def result(self, latency_ms: int, interrupted: Optional[str] = None) -> MemberResult:
        content = "".join(self.parts)
        if self.finish is None:
            reason = interrupted or "Mese stream ended without a finish chunk"
            return MemberResult(model=self.model, ok=False, content=content, latency_ms=latency_ms,
                                error_code="upstream_error", error_message=reason, retryable=True,
                                resolved=self.resolved)
        summary = self.finish.get("mese") if isinstance(self.finish.get("mese"), dict) else {}
        finish_reason = self.finish["choices"][0].get("finish_reason")
        wire_usage = self.usage if isinstance(self.usage, dict) else {}
        cost = summary.get("cost_usd") if "cost_usd" in summary else wire_usage.get("cost")
        result = MemberResult(
            model=self.model, ok=finish_reason in ("stop", "length"), content=content, latency_ms=latency_ms,
            ttft_ms=summary.get("ttft_ms"), cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
            usage=usage_from_wire(self.usage), finish_reason=finish_reason,
            resolved=summary.get("resolved") or self.resolved, warnings=list(summary.get("warnings") or []),
        )
        if result.ok and not content.strip():
            # Reasoning text is dropped, so a stream that only reasoned has nothing to rank or synthesize.
            why = "the token budget ran out, possibly during reasoning" if finish_reason == "length" else "empty answer"
            result.ok, result.error_code, result.retryable = False, "empty_response", False
            result.error_message = f"The model returned no answer text (finish_reason={finish_reason}: {why})"
            return result
        if result.ok:
            return result
        if finish_reason == "content_filter":
            category = next((w.split(":", 1)[1] for w in result.warnings if w.startswith("refusal:")), "unknown")
            result.error_code, result.error_message = "content_filtered", f"declined by provider ({category})"
        elif finish_reason == "error" and isinstance(self.finish.get("error"), dict):
            error = self.finish["error"]
            mese = error.get("mese") if isinstance(error.get("mese"), dict) else {}
            result.error_code = str(error.get("code") or "upstream_error")
            result.error_message = _truncate(str(error.get("message") or result.error_code))
            result.retryable = bool(mese.get("retryable", False))
        else:
            result.error_code = "upstream_error"
            result.error_message = f"unexpected finish_reason {finish_reason!r}"
            result.retryable = True
        return result


class MeseClient:
    """Async client for Mese's ``/v1`` API (one pooled connection pool per process)."""

    def __init__(self, base_url: str, token: Optional[str], transport: Optional[httpx.AsyncBaseTransport] = None, *,
                 timeout: httpx.Timeout = DEFAULT_TIMEOUT, models_ttl_s: float = MODELS_CACHE_TTL_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.base_url = base_url.rstrip("/")
        # trust_env=False: never route the bearer token through a proxy taken from the environment.
        self._http = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout,
                                       transport=transport, trust_env=False)
        self._models_ttl_s = models_ttl_s
        self._clock = clock
        self._models: Optional[Tuple[float, List[Dict[str, Any]]]] = None
        self._models_lock = asyncio.Lock()

    @property
    def display_url(self) -> str:
        return safe_url(self.base_url)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ── non-streaming calls ──────────────────────────────────────────────────

    async def _request_json(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        try:
            response = await self._http.request(method, path, json=body)
        except httpx.TransportError as exc:
            raise MeseError("mese_unreachable", f"Mese is unreachable at {self.display_url} ({type(exc).__name__})") from None
        if response.status_code >= 400:
            code, message, _ = parse_error_body(response.content, response.status_code)
            raise MeseError(code, message, response.status_code)
        try:
            return response.json()
        except ValueError:
            raise MeseError("upstream_error", f"Mese returned invalid JSON for {path}", response.status_code) from None

    async def list_models(self, force: bool = False) -> List[Dict[str, Any]]:
        """``GET /v1/models`` → V1Model dicts, cached for 10 s. Raises :class:`MeseError`."""
        async with self._models_lock:
            now = self._clock()
            if not force and self._models is not None and now - self._models[0] < self._models_ttl_s:
                return self._models[1]
            payload = await self._request_json("GET", "/v1/models")
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise MeseError("upstream_error", "Mese returned an unexpected /v1/models payload")
            models = [m for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)]
            self._models = (now, models)
            return models

    async def plan(self, models: List[str]) -> Dict[str, Any]:
        """``POST /v1/mese/plan`` → V1PlanResponse. Raises :class:`MeseError`."""
        payload = await self._request_json("POST", "/v1/mese/plan", {"models": models})
        if not isinstance(payload, dict):
            raise MeseError("upstream_error", "Mese returned an unexpected plan payload")
        return payload

    async def warmup(self, models: List[str], run_id: str) -> None:
        """``POST /v1/mese/warmup``; best-effort, every failure is swallowed (cancellation is not)."""
        try:
            await self._request_json("POST", "/v1/mese/warmup", {"models": models, "run_id": run_id})
        except Exception as exc:  # noqa: BLE001 - warmup is advisory
            log.info("Mese warmup failed for %s: %s", models, exc)

    async def status(self) -> Dict[str, Any]:
        """``GET /v1/mese/status`` → V1StatusResponse, or ``{"error": message, "code": code}``."""
        try:
            payload = await self._request_json("GET", "/v1/mese/status")
        except MeseError as exc:
            return {"error": exc.message, "code": exc.code}
        if not isinstance(payload, dict):
            return {"error": "Mese returned an unexpected status payload", "code": "upstream_error"}
        return payload

    # ── streaming chat ───────────────────────────────────────────────────────

    async def stream_chat(self, model: str, messages: List[Dict[str, Any]], *, stage: str, run_id: str,
                          conversation_id: str, on_delta: Optional[OnDelta] = None,
                          on_status: Optional[OnStatus] = None) -> MemberResult:
        """Streams one chat completion through Mese. Never returns None; never raises for upstream errors."""
        body = {
            "model": model, "messages": messages, "stream": True,
            "mese": {"client": CLIENT_NAME, "run_id": run_id, "conversation_id": conversation_id,
                     "stage": stage, "events": True},
        }
        started = time.monotonic()
        elapsed = lambda: int((time.monotonic() - started) * 1000)  # noqa: E731
        acc = _StreamAccumulator(model, on_delta, on_status)
        headers_received = False
        try:
            async with self._http.stream("POST", "/v1/chat/completions", json=body,
                                         headers={"Accept": "text/event-stream"}) as response:
                headers_received = True
                if response.status_code >= 400:
                    code, message, retryable = parse_error_body(await response.aread(), response.status_code)
                    return MemberResult(model=model, ok=False, latency_ms=elapsed(), error_code=code,
                                        error_message=message, retryable=retryable)
                async for event, data in iter_sse(response.aiter_lines()):
                    if await acc.feed(event, data):
                        break
        except httpx.TransportError as exc:
            if not headers_received:
                return MemberResult(model=model, ok=False, latency_ms=elapsed(), error_code="mese_unreachable",
                                    error_message=f"Mese is unreachable at {self.display_url} ({type(exc).__name__})",
                                    retryable=True)
            return acc.result(elapsed(), interrupted=f"connection to Mese lost ({type(exc).__name__})")
        except _MalformedStream as exc:
            return acc.result(elapsed(), interrupted=str(exc))
        return acc.result(elapsed())

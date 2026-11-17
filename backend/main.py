"""FastAPI backend for llm-council (Mese ARCHITECTURE §I.5, §I.6, §I.8).

Run it from the project root with ``uv run python -m backend.main [--host H] [--port P]``; it binds
``COUNCIL_HOST:COUNCIL_PORT`` (default ``0.0.0.0:8001``). The Mese client is opened and closed by the
app lifespan. CORS allows exactly ``COUNCIL_ALLOWED_ORIGINS`` without credentials, and the ``Host``
header must be one of :attr:`Settings.allowed_hosts` (400 ``invalid_host`` otherwise), which closes the
DNS-rebinding path around CORS.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, FrozenSet, List, Optional, Set

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from . import api_council
from .app_state import (AppContext, conversation_not_found, council_for_send, get_ctx, load_catalog,
                        resolve_preset_council, stored_preset_id, validate_council)
from .catalog import CatalogIndex
from .config import SETTINGS, Settings, host_of
from .contracts import CouncilConfigDict
from .council import RunnerDeps, TaskTracker, run_council
from .council import events as ev
from .mese_client import MeseClient
from .presets import LastUsedStore, PresetStore
from .schemas import CreateConversationRequest, SendMessageRequest
from .storage import DEFAULT_TITLE, ConversationStore, council_record

log = logging.getLogger("backend")

#: A ``: keepalive`` comment is sent after this much silence (cold local loads can be quiet for minutes).
KEEPALIVE_INTERVAL_S = 15.0
#: How long uvicorn lets open requests finish on SIGTERM before cancelling them (below the unit's TimeoutStopSec=30).
GRACEFUL_SHUTDOWN_S = 10
SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
CORS_METHODS = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
CORS_HEADERS = ["Content-Type", "Accept"]

_END = object()
_KEEPALIVE = object()

router = APIRouter()


class HostAllowlistMiddleware:
    """Answers 400 ``invalid_host`` unless the ``Host`` header names an allowed host.

    CORS does not apply once a DNS-rebinding page is same-origin with this port; its ``Host`` header
    still carries the attacker's name, so checking it keeps such a page away from the API.
    """

    def __init__(self, app: ASGIApp, allowed_hosts: FrozenSet[str]) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            raw = Headers(scope=scope).get("host", "")
            if host_of(raw) not in self.allowed_hosts:
                log.warning("rejected a request for Host %r (add its origin to COUNCIL_ALLOWED_ORIGINS)", raw[:100])
                detail = {"code": "invalid_host", "message": f"Host {raw[:100]!r} is not allowed"}
                await JSONResponse({"detail": detail}, status_code=400)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class SSEResponse(StreamingResponse):
    """A StreamingResponse that always closes its generator, even when the client vanished mid-send.

    Closing the generator runs its ``finally``, which cancels every council task and so closes every
    in-flight Mese stream.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                await aclose()


async def council_event_stream(query: str, council: CouncilConfigDict, conversation_id: str, deps: RunnerDeps, *,
                               is_first_message: bool, keepalive_s: float = KEEPALIVE_INTERVAL_S,
                               active_runs: Optional[Set[TaskTracker]] = None) -> AsyncIterator[str]:
    """Runs the council and yields its §I.6 events as SSE frames from one queue the runner feeds.

    The stream's tracker stays in ``active_runs`` until its tasks are done, so a shutdown that interrupts
    this generator can still cancel the run and let it store the turn.
    """
    queue: "asyncio.Queue[Any]" = asyncio.Queue()
    tracker = TaskTracker()
    if active_runs is not None:
        active_runs.add(tracker)

    async def emit(event: Dict[str, Any]) -> None:
        queue.put_nowait(event)

    async def run() -> None:
        try:
            await run_council(query, council, conversation_id, emit, deps=deps, is_first_message=is_first_message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the client as an `error` event
            log.exception("council run failed for conversation %s", conversation_id)
            queue.put_nowait(ev.error(f"{type(exc).__name__}: {exc}"[:500]))
        finally:
            queue.put_nowait(_END)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(keepalive_s)
            queue.put_nowait(_KEEPALIVE)

    tracker.spawn(run(), name=f"council:{conversation_id}")
    tracker.spawn(heartbeat(), name=f"keepalive:{conversation_id}")
    try:
        while True:
            item = await queue.get()
            if item is _END:
                break
            yield ": keepalive\n\n" if item is _KEEPALIVE else ev.to_sse(item)
    finally:
        # Client gone or run finished: cancel and await every task (this closes the Mese streams).
        await tracker.cancel_all()
        if active_runs is not None:
            active_runs.discard(tracker)


def _require_conversation(ctx: AppContext, conversation_id: str) -> Dict[str, Any]:
    conversation = ctx.store.get(conversation_id)
    if conversation is None:
        raise conversation_not_found()
    return conversation


def _needs_title(conversation: Dict[str, Any]) -> bool:
    """A title is generated while the conversation has the default one and no title call has finished yet.

    A first turn that was cancelled or crashed never reached its title call, so the next turn names the
    conversation. A title call that finished but failed (``title_attempted``) is not repeated: otherwise every
    later turn would pay for another title call and be stored as ``partial``.
    """
    title = conversation.get("title") or DEFAULT_TITLE
    return title == DEFAULT_TITLE and not conversation.get("title_attempted")


def _deps(ctx: AppContext, catalog: CatalogIndex) -> RunnerDeps:
    return RunnerDeps(client=ctx.client, store=ctx.store, catalog=catalog, title_model=ctx.settings.title_model)


@router.get("/")
async def root() -> Dict[str, str]:
    return {"status": "ok", "service": "LLM Council API"}


@router.get("/api/conversations")
async def list_conversations(request: Request) -> List[Dict[str, Any]]:
    return get_ctx(request).store.list()


@router.post("/api/conversations")
async def create_conversation(request: Request, body: Optional[CreateConversationRequest] = None) -> Dict[str, Any]:
    """Creates a conversation, optionally with a council (or a preset resolved into one).

    ``builtin:last-used`` is stored as the preset its council came from (see ``stored_preset_id``).
    """
    ctx = get_ctx(request)
    body = body or CreateConversationRequest()
    council = None
    if body.council is not None or body.preset_id is not None:
        catalog = await load_catalog(ctx)
        if body.council is not None:  # a council wins over preset_id
            checked = validate_council(list(body.council.members), body.council.chairman, catalog)
        else:
            checked = resolve_preset_council(ctx, body.preset_id or "", catalog)
        if checked is not None:
            council = council_record(checked["members"], checked["chairman"],
                                     stored_preset_id(ctx, body.preset_id, checked))
    return ctx.store.create(str(uuid.uuid4()), council)


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request) -> Dict[str, Any]:
    return _require_conversation(get_ctx(request), conversation_id)


@router.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, body: SendMessageRequest, request: Request) -> Dict[str, Any]:
    """Runs the full council and returns ``{stage1, stage2, stage3, metadata, failures, usage}``."""
    ctx = get_ctx(request)
    conversation = _require_conversation(ctx, conversation_id)
    council, catalog = await council_for_send(ctx, conversation, body.council.as_dict() if body.council else None)
    is_first_message = _needs_title(conversation)
    ctx.store.add_user_message(conversation_id, body.content)
    tracker = TaskTracker()
    ctx.active_runs.add(tracker)
    try:
        result = await tracker.spawn(run_council(body.content, council, conversation_id, deps=_deps(ctx, catalog),
                                                 is_first_message=is_first_message), name=f"council:{conversation_id}")
    finally:
        await tracker.cancel_all()
        ctx.active_runs.discard(tracker)
    return result.to_response()


@router.post("/api/conversations/{conversation_id}/message/stream/v2")
async def send_message_stream_v2(conversation_id: str, body: SendMessageRequest, request: Request) -> SSEResponse:
    """Streams the council run as §I.6 events. Validation errors are real HTTP statuses (before the stream)."""
    ctx = get_ctx(request)
    conversation = _require_conversation(ctx, conversation_id)
    council, catalog = await council_for_send(ctx, conversation, body.council.as_dict() if body.council else None)
    is_first_message = _needs_title(conversation)
    ctx.store.add_user_message(conversation_id, body.content)
    stream = council_event_stream(body.content, council, conversation_id, _deps(ctx, catalog),
                                  is_first_message=is_first_message, keepalive_s=KEEPALIVE_INTERVAL_S,
                                  active_runs=ctx.active_runs)
    return SSEResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


def create_app(settings: Optional[Settings] = None, *, mese_client: Optional[MeseClient] = None) -> FastAPI:
    """Builds the app. Tests pass ``settings`` and a ``mese_client`` backed by a mock transport."""
    settings = settings or SETTINGS
    ctx = AppContext(settings=settings, store=ConversationStore(settings.conversations_dir),
                     presets=PresetStore(settings.presets_path), last_used=LastUsedStore(settings.last_council_path),
                     mese=mese_client)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        owned = ctx.mese is None
        if owned:
            ctx.mese = MeseClient(settings.mese_url, settings.mese_api_key)
        log.info("llm-council using %s", settings.describe())
        if not settings.token_set:
            log.warning("MESE_API_KEY is not set: every Mese call will be rejected. Add it to .env.")
        try:
            yield
        finally:
            # Shutdown: uvicorn has cancelled the open requests (GRACEFUL_SHUTDOWN_S), but a run task may not
            # have had a turn yet, and uvicorn re-raises SIGTERM right after this. Cancel and await every run,
            # so each stores its turn as 'cancelled' before the Mese client closes.
            for tracker in list(ctx.active_runs):
                await tracker.cancel_all()
            ctx.active_runs.clear()
            if owned and ctx.mese is not None:
                await ctx.mese.aclose()
                ctx.mese = None

    app = FastAPI(title="LLM Council API", lifespan=lifespan)
    app.state.ctx = ctx
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=False,
                       allow_methods=CORS_METHODS, allow_headers=CORS_HEADERS)
    if settings.allowed_hosts is not None:  # added last, so it runs first (before CORS)
        app.add_middleware(HostAllowlistMiddleware, allowed_hosts=settings.allowed_hosts)
    app.include_router(api_council.router)
    app.include_router(router)
    return app


app = create_app()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="llm-council backend (a Mese client)")
    parser.add_argument("--host", default=SETTINGS.council_host, help="bind address (COUNCIL_HOST)")
    parser.add_argument("--port", type=int, default=SETTINGS.council_port, help="bind port (COUNCIL_PORT)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn

    # On SIGTERM uvicorn otherwise waits for every open SSE stream without limit (a council turn can run for
    # minutes) and systemd SIGKILLs it after TimeoutStopSec, losing the turn. After the grace period it
    # cancels the request tasks, and the runner stores the turn as 'cancelled'.
    uvicorn.run(app, host=args.host, port=args.port, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S)


if __name__ == "__main__":
    main()

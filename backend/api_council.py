"""Council, catalog and preset routes (Mese ARCHITECTURE §I.5).

``GET /api/models``, ``GET /api/mese/health``, ``POST /api/council/plan``, ``GET /api/presets``,
``PUT|DELETE /api/presets/{id}`` and ``PUT /api/conversations/{id}/council``. The Mese token never
appears in any response.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from fastapi import APIRouter, Body, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from .app_state import (conversation_not_found, get_ctx, http_error, load_catalog, mese_http_error,
                        validate_council)
from .config import safe_url
from .mese_client import MeseError
from .presets import PresetNotFound, list_presets, user_preset_view
from .schemas import ConversationCouncilPut, CouncilConfig, PresetPutRequest, is_builtin_preset_id, is_user_preset_id

router = APIRouter()

_STATUS_FIELDS = ("version", "locked", "nodes_up", "nodes_total", "providers_configured", "breakers_open")


@router.get("/api/models")
async def get_models(request: Request, refresh: bool = False) -> Dict[str, Any]:
    """The Mese catalog projected for the picker; 503 when Mese is down.

    ``?refresh=1`` skips the client's 10 s catalog cache (the UI asks for it when Mese's health changed,
    e.g. right after a key was added in the Mese admin panel); presets fetched next see the same list.
    """
    ctx = get_ctx(request)
    catalog = await load_catalog(ctx, force=refresh)
    return {"mese": {"ok": True, "admin_url": ctx.settings.mese_admin_url}, "models": catalog.models}


@router.get("/api/mese/health")
async def mese_health(request: Request) -> Dict[str, Any]:
    """Read-only Mese status for the sidebar pill. Always 200; ``ok`` says whether Mese answered."""
    ctx = get_ctx(request)
    settings = ctx.settings
    started = time.monotonic()
    status = await ctx.client.status()
    latency_ms = int((time.monotonic() - started) * 1000)
    error = status.get("error")
    if error and not settings.token_set and status.get("code") == "unauthorized":
        error = "MESE_API_KEY is not set in llm-council's .env"
    health: Dict[str, Any] = {
        "ok": error is None,
        "url": safe_url(settings.mese_url),
        "admin_url": settings.mese_admin_url,
        "token_set": settings.token_set,
        "latency_ms": latency_ms if status.get("code") != "mese_unreachable" else None,
    }
    for name in _STATUS_FIELDS:
        health[name] = None if error else status.get(name)
    health["error"] = error
    return health


@router.post("/api/council/plan")
async def council_plan(council: CouncilConfig, request: Request) -> Dict[str, Any]:
    """Proxies ``POST /v1/mese/plan`` for the members plus the chairman (the picker debounces it)."""
    models = list(dict.fromkeys([*council.members, council.chairman]))
    try:
        return await get_ctx(request).client.plan(models)
    except MeseError as exc:
        raise mese_http_error(exc) from None


@router.get("/api/presets")
async def get_presets(request: Request) -> Dict[str, Any]:
    """Built-ins resolved against the current catalog, then user presets."""
    ctx = get_ctx(request)
    catalog = await load_catalog(ctx)
    return {"presets": list_presets(ctx.presets, catalog)}


def _reject_builtin(preset_id: str) -> None:
    if is_builtin_preset_id(preset_id):
        raise http_error(409, "preset_immutable", "Built-in presets cannot be changed or deleted")


@router.put("/api/presets/{preset_id}")
async def put_preset(preset_id: str, request: Request, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Creates or replaces a user preset; the council is checked against the catalog. 409 for built-ins."""
    _reject_builtin(preset_id)  # before body validation: built-ins are immutable whatever the body
    if not is_user_preset_id(preset_id):
        raise http_error(422, "invalid_preset_id", "Preset ids are 1-40 characters of a-z, 0-9 and '-'")
    try:
        preset = PresetPutRequest.model_validate(body)
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False)
        raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in errors]) from None
    ctx = get_ctx(request)
    catalog = await load_catalog(ctx)
    council = validate_council(list(preset.members), preset.chairman, catalog)
    record = ctx.presets.put(preset_id, preset.name, council["members"], council["chairman"])
    return user_preset_view(record, catalog)


@router.delete("/api/presets/{preset_id}", status_code=204)
async def delete_preset(preset_id: str, request: Request) -> Response:
    _reject_builtin(preset_id)
    try:
        if not is_user_preset_id(preset_id):
            raise PresetNotFound(preset_id)
        get_ctx(request).presets.delete(preset_id)
    except PresetNotFound:
        raise http_error(404, "preset_not_found", f"Unknown preset {preset_id!r}") from None
    return Response(status_code=204)


@router.put("/api/conversations/{conversation_id}/council")
async def put_conversation_council(conversation_id: str, body: ConversationCouncilPut,
                                   request: Request) -> Dict[str, Any]:
    """Saves the council for the conversation's *next* message."""
    ctx = get_ctx(request)
    if ctx.store.get(conversation_id) is None:
        raise conversation_not_found()
    catalog = await load_catalog(ctx)
    council = validate_council(list(body.members), body.chairman, catalog)
    saved = ctx.store.set_council(conversation_id, council["members"], council["chairman"], body.preset_id)
    return {"council": saved}

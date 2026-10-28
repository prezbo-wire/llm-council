"""Shared state and HTTP helpers for the FastAPI routes (``main.py`` and ``api_council.py``).

Error bodies (§I.5): ``{"detail": {"code": …, …}}``.

- 422 ``unknown_models`` / ``chairman_cannot_chair`` / ``council_required``;
- 503 ``mese_unreachable`` when Mese cannot be reached; 502 when Mese answered with an error;
- 404 ``conversation_not_found`` / ``preset_not_found``; 409 ``preset_immutable``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from .catalog import CatalogIndex, CouncilValidationError, normalize_council
from .config import Settings
from .mese_client import MeseClient, MeseError
from .presets import PresetNotFound, PresetStore, resolve_builtin, resolve_preset
from .storage import ConversationStore


@dataclass
class AppContext:
    settings: Settings
    store: ConversationStore
    presets: PresetStore
    mese: Optional[MeseClient] = None

    @property
    def client(self) -> MeseClient:
        if self.mese is None:
            raise RuntimeError("the Mese client is not started (the app lifespan did not run)")
        return self.mese


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def http_error(status: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def mese_http_error(exc: MeseError) -> HTTPException:
    """503 when Mese is down, 502 when it answered with an error (e.g. a rejected token)."""
    return http_error(503 if exc.unreachable else 502, exc.code, exc.message)


def validation_http_error(exc: CouncilValidationError) -> HTTPException:
    return HTTPException(status_code=422, detail=exc.detail())


def conversation_not_found() -> HTTPException:
    # Keeps the legacy string detail the frontend already handles.
    return HTTPException(status_code=404, detail="Conversation not found")


async def load_catalog(ctx: AppContext, force: bool = False) -> CatalogIndex:
    try:
        return CatalogIndex(await ctx.client.list_models(force=force))
    except MeseError as exc:
        raise mese_http_error(exc) from None


def validate_council(members: List[str], chairman: str, catalog: CatalogIndex) -> Dict[str, Any]:
    """Semantic checks (§I.5) → ``{"members", "chairman"}`` with canonical ids, or HTTP 422."""
    try:
        return normalize_council(members, chairman, catalog)
    except CouncilValidationError as exc:
        raise validation_http_error(exc) from None


def resolve_preset_council(ctx: AppContext, preset_id: str, catalog: CatalogIndex) -> Optional[Dict[str, Any]]:
    """A preset as a validated council, or None when a built-in cannot resolve. 404 for unknown presets."""
    try:
        preset = resolve_preset(preset_id, ctx.presets, catalog)
    except PresetNotFound:
        raise http_error(404, "preset_not_found", f"Unknown preset {preset_id!r}") from None
    if not preset["members"] or not preset["chairman"]:
        return None
    return validate_council(preset["members"], preset["chairman"], catalog)


def _fallback_council(ctx: AppContext, catalog: CatalogIndex) -> Tuple[Dict[str, Any], Optional[str]]:
    """``DEFAULT_COUNCIL``/``DEFAULT_CHAIRMAN`` from env, else the resolved ``builtin:hybrid``, else 422."""
    settings = ctx.settings
    if settings.default_council and settings.default_chairman:
        return validate_council(list(settings.default_council), settings.default_chairman, catalog), None
    hybrid = resolve_builtin("builtin:hybrid", catalog)
    if hybrid["members"] and hybrid["chairman"]:
        return validate_council(hybrid["members"], hybrid["chairman"], catalog), "builtin:hybrid"
    raise http_error(422, "council_required",
                     "No council given and no default could be resolved; pick council members first.")


async def council_for_send(ctx: AppContext, conversation: Dict[str, Any],
                           requested: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], CatalogIndex]:
    """Chooses, validates and persists the council for the next message.

    Order: the request's council, then the conversation's council, then the defaults. The chosen council
    is saved as the conversation's council (a request council keeps the saved ``preset_id`` only when it
    is unchanged).
    """
    catalog = await load_catalog(ctx)
    saved = conversation.get("council") or None
    preset_id: Optional[str] = None
    if requested is not None:
        council = validate_council(requested["members"], requested["chairman"], catalog)
        if saved and saved.get("members") == council["members"] and saved.get("chairman") == council["chairman"]:
            preset_id = saved.get("preset_id")
    elif saved and saved.get("members") and saved.get("chairman"):
        council = validate_council(list(saved["members"]), saved["chairman"], catalog)
        preset_id = saved.get("preset_id")
    else:
        council, preset_id = _fallback_council(ctx, catalog)
    unchanged = (saved and saved.get("members") == council["members"] and saved.get("chairman") == council["chairman"]
                 and saved.get("preset_id") == preset_id)
    if not unchanged:
        ctx.store.set_council(conversation["id"], council["members"], council["chairman"], preset_id)
    return council, catalog

"""Council presets (Mese ARCHITECTURE §I.5).

Built-in presets are *selectors* resolved against the current Mese catalog at request time, so they
stay valid as models come and go. User presets are stored in ``data/presets.json`` as
``{"version": 1, "presets": [{id, name, members, chairman, created_at, updated_at}]}`` and written
atomically.

=========================  ====================================================  ==========================
Built-in                   Members                                               Chairman
=========================  ====================================================  ==========================
``builtin:all-local``      up to 4 available local/custom models with            the resident local model
                           ``can_rank``: resident first, then loaded, then       with ``can_chair`` and the
                           largest size; models Mese runs exclusively only       largest context (ties:
                           to reach 2 members (largest first)                    size), else any available
                                                                                 local ``can_chair`` model
                                                                                 by the same rule
``builtin:frontier-cloud`` available API-key cloud models tagged ``frontier``    the ``default_chair`` model,
                           (max 5)                                               else the first frontier
                                                                                 model with ``can_chair``
``builtin:hybrid``         the 2 cheapest available API-key frontier cloud       as frontier-cloud
                           models plus the first 2 of the all-local selection
=========================  ====================================================  ==========================

Residency is Mese's effective class (``CouncilModel.residency``, Mese ARCHITECTURE §G.6): resident models stay
loaded, so an all-local council of residents swaps no model in or out. A model with no residency data (an older
Mese) counts as on_demand, which gives exactly the v1 selection. A big exclusive chair is a user preset or a
manual pick, never a built-in.

**Billing.** The built-ins never pick a model billed to the user's ChatGPT plan (``chatgpt/*``, Mese ``billing``
``chatgpt_plan``), even one tagged ``frontier`` or ``default_chair``. A built-in is re-resolved on every request, so
including plan models would make the same preset switch between plan usage and API-key billing whenever ChatGPT is
connected, paused at a usage limit or resumed (and Hybrid's "cheapest" rule would always pick the $0 plan model).
Plan models join a council only when picked by hand or saved in a user preset, and no preset ever substitutes
``openai/gpt-6.1-sol`` for ``chatgpt/gpt-6.1-sol`` or the reverse: ids are kept as stored, and alias resolution
never crosses billing (:class:`backend.catalog.CatalogIndex`).

``builtin:last-used`` ("Last used", listed first) is the council of the most recent message a send route
accepted, kept in ``<data_dir>/last_council.json`` by :class:`LastUsedStore`. It is a moving pointer, so it
is never stored as a conversation's ``preset_id`` (:func:`backend.app_state.stored_preset_id`).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catalog import LOCAL_KINDS, CatalogIndex, is_plan_billed
from .schemas import (BUILTIN_PRESET_IDS, MAX_MEMBERS, PRESET_REF_PATTERN, CouncilModel, Preset, is_model_id,
                      is_user_preset_id)
from .storage import atomic_write_json, utc_now_iso

log = logging.getLogger(__name__)

PRESETS_FILE_VERSION = 1
LAST_USED_FILE_VERSION = 1
#: Not one of ``BUILTIN_PRESET_IDS`` (those are catalog selectors); still immutable (409 on PUT/DELETE).
LAST_USED_PRESET_ID = "builtin:last-used"
_PRESET_REF_RE = re.compile(PRESET_REF_PATTERN)
ALL_LOCAL_MAX = 4
#: Exclusive models join the all-local members only while there are fewer than this many.
LOCAL_MIN_MEMBERS = 2
FRONTIER_MAX = 5
HYBRID_CLOUD = 2
HYBRID_LOCAL = 2

_BUILTIN_META = {
    "builtin:all-local": ("All local", "Local models only, resident ones first: nothing leaves your machines and "
                                       "runs cost $0."),
    "builtin:frontier-cloud": ("Frontier cloud", "The available frontier cloud models on API keys (never your "
                                                 "ChatGPT plan); the default chair leads."),
    "builtin:hybrid": ("Hybrid", "The two cheapest frontier cloud models on API keys plus two local models."),
}
assert tuple(_BUILTIN_META) == BUILTIN_PRESET_IDS


class PresetNotFound(KeyError):
    """No user preset with that id."""


class LastUsedPreset(Preset, total=False):
    """The ``builtin:last-used`` entry: a ``Preset`` plus where its council came from."""

    source_preset_id: Optional[str]
    conversation_id: Optional[str]


# ── built-in selectors ───────────────────────────────────────────────────────


def _available(models: List[CouncilModel]) -> List[CouncilModel]:
    return [m for m in models if m["available"]]


def residency_of(model: CouncilModel) -> str:
    """The model's effective residency class; no residency data (cloud, an older Mese) counts as on_demand."""
    residency = model.get("residency")
    return residency["effective"] if residency else "on_demand"


def _by_size(model: CouncilModel) -> int:
    return -(model["size_bytes"] or 0)


def select_local_members(models: List[CouncilModel], limit: int = ALL_LOCAL_MAX) -> List[str]:
    """Available local/custom rankers: resident first, then loaded, then by size descending (stable).

    Exclusive models (configured, or "auto" because they cannot fit beside the resident set) unload residents
    while they run, so they are appended, largest first, only while fewer than 2 members were found.
    """
    candidates = [m for m in _available(models) if m["kind"] in LOCAL_KINDS and m["can_rank"]]
    primary = [m for m in candidates if residency_of(m) != "exclusive"]
    primary.sort(key=lambda m: (residency_of(m) != "resident", m["load_state"] != "loaded", _by_size(m)))
    picked = primary[:limit]
    wanted = min(LOCAL_MIN_MEMBERS, limit)
    if len(picked) < wanted:
        exclusive = sorted((m for m in candidates if residency_of(m) == "exclusive"), key=_by_size)
        picked += exclusive[: wanted - len(picked)]
    return [m["id"] for m in picked]


def select_local_chair(models: List[CouncilModel]) -> Optional[str]:
    """The resident local ``can_chair`` model with the largest context, ties broken by largest size.

    With no resident chair, the same rule over every available local ``can_chair`` model, so an exclusive
    model still chairs when nothing is resident.
    """
    candidates = [m for m in _available(models) if m["kind"] in LOCAL_KINDS and m["can_chair"]]
    if not candidates:
        return None
    residents = [m for m in candidates if residency_of(m) == "resident"]
    best = max(residents or candidates, key=lambda m: (m["context_length"] or 0, m["size_bytes"] or 0))
    return best["id"]


def _plan(model: CouncilModel) -> bool:
    """Billed to the ChatGPT plan: built-ins never pick such a model (see Billing above)."""
    return is_plan_billed(model["id"], model.get("billing"))


def _api_key_cloud(models: List[CouncilModel]) -> List[CouncilModel]:
    return [m for m in _available(models) if m["kind"] == "cloud" and not _plan(m)]


def _frontier(models: List[CouncilModel]) -> List[CouncilModel]:
    return [m for m in _api_key_cloud(models) if "frontier" in m["tags"]]


def select_frontier_members(models: List[CouncilModel], limit: int = FRONTIER_MAX) -> List[str]:
    return [m["id"] for m in _frontier(models)[:limit]]


def select_cloud_chair(models: List[CouncilModel]) -> Optional[str]:
    """The available ``default_chair`` model, else the first available frontier model with ``can_chair``; never a
    ChatGPT-plan model."""
    for model in _available(models):
        if "default_chair" in model["tags"] and model["can_chair"] and not _plan(model):
            return model["id"]
    return next((m["id"] for m in _frontier(models) if m["can_chair"]), None)


def _blended_price(model: CouncilModel) -> float:
    price = model["price_per_mtok"]
    return price["input"] + price["output"] if price else float("inf")


def select_cheapest_frontier(models: List[CouncilModel], count: int = HYBRID_CLOUD) -> List[str]:
    """The ``count`` cheapest available frontier models by input + output price (catalog order on ties)."""
    ranked = sorted(_frontier(models), key=_blended_price)
    return [m["id"] for m in ranked[:count]]


def resolve_builtin(preset_id: str, catalog: CatalogIndex) -> Preset:
    """Resolves a ``builtin:*`` selector against the catalog; ``chairman`` is None when it can't resolve."""
    models = catalog.models
    if preset_id == "builtin:all-local":
        members, chairman = select_local_members(models), select_local_chair(models)
    elif preset_id == "builtin:frontier-cloud":
        members, chairman = select_frontier_members(models), select_cloud_chair(models)
    elif preset_id == "builtin:hybrid":
        members = select_cheapest_frontier(models) + select_local_members(models, HYBRID_LOCAL)
        chairman = select_cloud_chair(models)
    else:
        raise PresetNotFound(preset_id)
    if not members:
        chairman = None
    name, description = _BUILTIN_META[preset_id]
    return {"id": preset_id, "name": name, "description": description, "members": members, "chairman": chairman,
            "builtin": True, "unavailable": [], "created_at": None, "updated_at": None}


# ── user presets ─────────────────────────────────────────────────────────────


def _valid_record(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str) and is_user_preset_id(record["id"])
            and isinstance(record.get("name"), str) and isinstance(record.get("members"), list)
            and all(isinstance(m, str) and is_model_id(m) for m in record["members"])
            and isinstance(record.get("chairman"), str) and is_model_id(record["chairman"]))


class PresetStore:
    """User presets in one JSON file, rewritten atomically on every change."""

    def __init__(self, path: os.PathLike) -> None:
        self.path = Path(path)

    def _load(self) -> List[Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            log.error("presets file %s is unreadable (%s); starting empty", self.path, exc)
            self._quarantine()
            return []
        records = data.get("presets") if isinstance(data, dict) else None
        if not isinstance(records, list):
            return []
        valid = [r for r in records if _valid_record(r)]
        if len(valid) != len(records):
            log.warning("ignored %d invalid preset record(s) in %s", len(records) - len(valid), self.path)
        return valid

    def _quarantine(self) -> None:
        """Keeps an unreadable presets file aside so the next write cannot destroy it."""
        backup = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}")
        try:
            os.replace(self.path, backup)
            log.error("moved unreadable presets file to %s", backup)
        except OSError:
            pass

    def _write(self, records: List[Dict[str, Any]]) -> None:
        atomic_write_json(self.path, {"version": PRESETS_FILE_VERSION, "presets": records})

    def list(self) -> List[Dict[str, Any]]:
        return self._load()

    def get(self, preset_id: str) -> Optional[Dict[str, Any]]:
        return next((r for r in self._load() if r["id"] == preset_id), None)

    def put(self, preset_id: str, name: str, members: List[str], chairman: str) -> Dict[str, Any]:
        """Creates or replaces a user preset (``created_at`` survives updates)."""
        if not is_user_preset_id(preset_id):
            raise ValueError(f"invalid preset id {preset_id!r}")
        records = self._load()
        now = utc_now_iso()
        existing = next((r for r in records if r["id"] == preset_id), None)
        record = {"id": preset_id, "name": name, "members": list(members), "chairman": chairman,
                  "created_at": existing.get("created_at", now) if existing else now, "updated_at": now}
        records = [record if r["id"] == preset_id else r for r in records] if existing else records + [record]
        self._write(records)
        return record

    def delete(self, preset_id: str) -> None:
        records = self._load()
        remaining = [r for r in records if r["id"] != preset_id]
        if len(remaining) == len(records):
            raise PresetNotFound(preset_id)
        self._write(remaining)


def user_preset_view(record: Dict[str, Any], catalog: CatalogIndex) -> Preset:
    """A stored preset as a ``Preset``; ``unavailable`` lists ids that are unknown or unavailable now."""
    ids = list(dict.fromkeys(record["members"] + [record["chairman"]]))
    return {"id": record["id"], "name": record["name"], "description": None, "members": list(record["members"]),
            "chairman": record["chairman"], "builtin": False,
            "unavailable": [m for m in ids if not catalog.is_available(m)],
            "created_at": record.get("created_at"), "updated_at": record.get("updated_at")}


# ── last used ────────────────────────────────────────────────────────────────


def _valid_last_used(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    members, chairman, preset_id = record.get("members"), record.get("chairman"), record.get("preset_id")
    return (isinstance(members, list) and 1 <= len(members) <= MAX_MEMBERS
            and all(isinstance(m, str) and is_model_id(m) for m in members) and len(set(members)) == len(members)
            and isinstance(chairman, str) and is_model_id(chairman)
            and (preset_id is None or (isinstance(preset_id, str) and preset_id != LAST_USED_PRESET_ID
                                       and bool(_PRESET_REF_RE.match(preset_id))))
            and isinstance(record.get("conversation_id"), str))


class LastUsedStore:
    """The council of the most recent accepted send: one global record (a single-user app), rewritten
    atomically on every send as ``{"version": 1, "council": {members, chairman, preset_id, conversation_id,
    used_at}}``. Concurrent sends: the last write wins.
    """

    def __init__(self, path: os.PathLike) -> None:
        self.path = Path(path)

    def get(self) -> Optional[Dict[str, Any]]:
        """The stored record, or None when there is none or it is unreadable or invalid (the next send
        overwrites it)."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            log.warning("last-used council file %s is unreadable (%s); ignoring it", self.path, exc)
            return None
        versioned = isinstance(data, dict) and data.get("version") == LAST_USED_FILE_VERSION
        record = data.get("council") if versioned else None
        if not _valid_last_used(record):
            log.warning("ignored an invalid last-used council in %s", self.path)
            return None
        used_at = record.get("used_at")
        return {"members": list(record["members"]), "chairman": record["chairman"], "preset_id": record.get("preset_id"),
                "conversation_id": record["conversation_id"], "used_at": used_at if isinstance(used_at, str) else None}

    def record(self, members: List[str], chairman: str, preset_id: Optional[str],
               conversation_id: str) -> Dict[str, Any]:
        """Stores a validated, canonical council; the moving ``builtin:last-used`` pointer is never its source."""
        record = {"members": list(members), "chairman": chairman,
                  "preset_id": None if preset_id == LAST_USED_PRESET_ID else preset_id,
                  "conversation_id": conversation_id, "used_at": utc_now_iso()}
        atomic_write_json(self.path, {"version": LAST_USED_FILE_VERSION, "council": record})
        return record


def resolve_last_used(record: Optional[Dict[str, Any]], catalog: CatalogIndex) -> LastUsedPreset:
    """The last-used council as a preset, resolved against the catalog now; empty before any send.

    Aliases become catalog ids. Unlike user presets, ids that left the catalog are dropped: the picker lists
    only catalog models, so they could never be unchecked, and a send would be 422 ``unknown_models``. They
    are listed in ``unavailable`` with the kept ids that are unavailable now (those fail fast at run time).
    ``chairman`` is None when it left the catalog, can no longer chair, or no member is left.
    """
    preset: LastUsedPreset = {
        "id": LAST_USED_PRESET_ID, "name": "Last used", "description": "The council of your most recent question.",
        "members": [], "chairman": None, "builtin": True, "unavailable": [], "created_at": None, "updated_at": None,
        "source_preset_id": None, "conversation_id": None}
    if record is None:
        return preset
    canonical = [catalog.canonical(m) for m in record["members"]]
    members = list(dict.fromkeys(m for m in canonical if m is not None))
    chair_id = catalog.canonical(record["chairman"])
    chair = catalog.get(chair_id) if chair_id else None
    chairman = chair_id if members and chair and chair["can_chair"] else None
    gone = [m for m in dict.fromkeys(record["members"] + [record["chairman"]]) if catalog.canonical(m) is None]
    kept = members + ([chairman] if chairman else [])
    preset.update(members=members, chairman=chairman,
                  unavailable=list(dict.fromkeys(gone + [m for m in kept if not catalog.is_available(m)])),
                  updated_at=record.get("used_at"), source_preset_id=record.get("preset_id"),
                  conversation_id=record.get("conversation_id"))
    return preset


def list_presets(store: PresetStore, catalog: CatalogIndex,
                 last_used: Optional[Dict[str, Any]] = None) -> List[Preset]:
    """Last used first, then the built-ins (resolved now), then user presets in creation order."""
    return [resolve_last_used(last_used, catalog)] + [resolve_builtin(pid, catalog) for pid in BUILTIN_PRESET_IDS] + \
        [user_preset_view(r, catalog) for r in store.list()]


def resolve_preset(preset_id: str, store: PresetStore, catalog: CatalogIndex,
                   last_used: Optional[Dict[str, Any]] = None) -> Preset:
    """A built-in, Last used (from the ``last_used`` record) or user preset by id. Raises :class:`PresetNotFound`."""
    if preset_id == LAST_USED_PRESET_ID:
        return resolve_last_used(last_used, catalog)
    if preset_id.startswith("builtin:"):
        return resolve_builtin(preset_id, catalog)
    record = store.get(preset_id)
    if record is None:
        raise PresetNotFound(preset_id)
    return user_preset_view(record, catalog)

"""Council presets (Mese ARCHITECTURE §I.5).

Built-in presets are *selectors* resolved against the current Mese catalog at request time, so they
stay valid as models come and go. User presets are stored in ``data/presets.json`` as
``{"version": 1, "presets": [{id, name, members, chairman, created_at, updated_at}]}`` and written
atomically.

=========================  ====================================================  ==========================
Built-in                   Members                                               Chairman
=========================  ====================================================  ==========================
``builtin:all-local``      up to 4 available local/custom models with           the available local model
                           ``can_rank``: loaded first, then largest size         with ``can_chair`` and the
                                                                                 largest context (ties: size)
``builtin:frontier-cloud`` available cloud models tagged ``frontier`` (max 5)    the ``default_chair`` model,
                                                                                 else the first frontier
                                                                                 model with ``can_chair``
``builtin:hybrid``         the 2 cheapest available frontier cloud models plus   as frontier-cloud
                           the first 2 of the all-local selection
=========================  ====================================================  ==========================
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catalog import LOCAL_KINDS, CatalogIndex
from .schemas import BUILTIN_PRESET_IDS, CouncilModel, Preset, is_model_id, is_user_preset_id
from .storage import atomic_write_json, utc_now_iso

log = logging.getLogger(__name__)

PRESETS_FILE_VERSION = 1
ALL_LOCAL_MAX = 4
FRONTIER_MAX = 5
HYBRID_CLOUD = 2
HYBRID_LOCAL = 2

_BUILTIN_META = {
    "builtin:all-local": ("All local", "Local models only: nothing leaves your machines and runs cost $0."),
    "builtin:frontier-cloud": ("Frontier cloud", "The available frontier cloud models; the default chair leads."),
    "builtin:hybrid": ("Hybrid", "The two cheapest frontier cloud models plus two local models."),
}
assert tuple(_BUILTIN_META) == BUILTIN_PRESET_IDS


class PresetNotFound(KeyError):
    """No user preset with that id."""


# ── built-in selectors ───────────────────────────────────────────────────────


def _available(models: List[CouncilModel]) -> List[CouncilModel]:
    return [m for m in models if m["available"]]


def select_local_members(models: List[CouncilModel], limit: int = ALL_LOCAL_MAX) -> List[str]:
    """Available local/custom rankers: loaded ones first, then by size descending (stable)."""
    candidates = [m for m in _available(models) if m["kind"] in LOCAL_KINDS and m["can_rank"]]
    candidates.sort(key=lambda m: (m["load_state"] != "loaded", -(m["size_bytes"] or 0)))
    return [m["id"] for m in candidates[:limit]]


def select_local_chair(models: List[CouncilModel]) -> Optional[str]:
    """The available local ``can_chair`` model with the largest context, ties broken by largest size."""
    candidates = [m for m in _available(models) if m["kind"] in LOCAL_KINDS and m["can_chair"]]
    if not candidates:
        return None
    best = max(candidates, key=lambda m: (m["context_length"] or 0, m["size_bytes"] or 0))
    return best["id"]


def _frontier(models: List[CouncilModel]) -> List[CouncilModel]:
    return [m for m in _available(models) if m["kind"] == "cloud" and "frontier" in m["tags"]]


def select_frontier_members(models: List[CouncilModel], limit: int = FRONTIER_MAX) -> List[str]:
    return [m["id"] for m in _frontier(models)[:limit]]


def select_cloud_chair(models: List[CouncilModel]) -> Optional[str]:
    """The available ``default_chair`` model, else the first available frontier model with ``can_chair``."""
    for model in _available(models):
        if "default_chair" in model["tags"] and model["can_chair"]:
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


def list_presets(store: PresetStore, catalog: CatalogIndex) -> List[Preset]:
    """Built-ins (resolved now) first, then user presets in creation order."""
    return [resolve_builtin(pid, catalog) for pid in BUILTIN_PRESET_IDS] + \
        [user_preset_view(r, catalog) for r in store.list()]


def resolve_preset(preset_id: str, store: PresetStore, catalog: CatalogIndex) -> Preset:
    """A built-in or user preset by id. Raises :class:`PresetNotFound`."""
    if preset_id.startswith("builtin:"):
        return resolve_builtin(preset_id, catalog)
    record = store.get(preset_id)
    if record is None:
        raise PresetNotFound(preset_id)
    return user_preset_view(record, catalog)

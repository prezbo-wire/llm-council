"""Mese catalog for llm-council (Mese ARCHITECTURE §I.5).

Projects Mese's ``V1Model`` entries to the picker's ``CouncilModel`` shape, resolves member metadata
for runs and snapshots, and performs the semantic council checks:

- an id not in the catalog → 422 ``{"code": "unknown_models", "bad_ids": [...]}``;
- a chairman without ``can_chair`` → 422 ``{"code": "chairman_cannot_chair"}``;
- unavailable ids are allowed (they fail fast at run time with an explicit ``member_error``).

Mese being down surfaces as :class:`backend.mese_client.MeseError` (mapped to 503 by the API).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from .config import LEGACY_DISPLAY_NAMES
from .schemas import CouncilModel

LOCAL_KINDS = ("local", "custom")


class CouncilValidationError(Exception):
    """A council that fails the semantic checks against the Mese catalog (HTTP 422)."""

    def __init__(self, code: str, message: str, *, bad_ids: Optional[List[str]] = None, model: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.bad_ids = bad_ids
        self.model = model

    def detail(self) -> Dict[str, Any]:
        detail: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.bad_ids is not None:
            detail["bad_ids"] = self.bad_ids
        if self.model is not None:
            detail["model"] = self.model
        return detail


@dataclass(frozen=True)
class MemberInfo:
    """What a run needs to know about one council model (from the catalog, or a fallback)."""

    id: str
    display_name: str
    kind: str
    provider_id: Optional[str]
    node_id: Optional[str]
    upstream_model: str
    can_rank: bool
    can_chair: bool
    context_length: Optional[int]
    lineage: Optional[Dict[str, Any]]
    in_catalog: bool = True

    @property
    def is_local(self) -> bool:
        return self.kind in LOCAL_KINDS

    def snapshot(self, *, chairman: bool = False) -> Dict[str, Any]:
        """``council_snapshot`` entry (§I.7)."""
        entry: Dict[str, Any] = {"id": self.id, "display_name": self.display_name, "kind": self.kind,
                                 "provider_id": self.provider_id, "node_id": self.node_id,
                                 "upstream_model": self.upstream_model}
        if not chairman:
            entry["can_rank"] = self.can_rank
            entry["lineage"] = self.lineage
        return entry


def usd_per_mtok(value: Any) -> Optional[float]:
    """Converts a USD-per-token decimal string (``V1Model.pricing``) to USD per 1M tokens (exact math)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        per_token = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not per_token.is_finite() or per_token < 0:
        return None
    return float(per_token * 1_000_000)


def price_per_mtok(model: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """``{input, output}`` USD per 1M tokens for cloud models; None for local/custom or unknown prices."""
    if (model.get("mese") or {}).get("kind") not in ("cloud",):
        return None
    pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
    prompt, completion = usd_per_mtok(pricing.get("prompt")), usd_per_mtok(pricing.get("completion"))
    if prompt is None or completion is None:
        return None
    return {"input": prompt, "output": completion}


def project_model(model: Dict[str, Any]) -> CouncilModel:
    """``V1Model`` → ``CouncilModel`` (§I.5). No base URL or credential metadata is ever copied."""
    mese = model.get("mese") if isinstance(model.get("mese"), dict) else {}
    provider = mese.get("provider") if isinstance(mese.get("provider"), dict) else None
    node = mese.get("node") if isinstance(mese.get("node"), dict) else None
    lineage = mese.get("lineage") if isinstance(mese.get("lineage"), dict) else None
    return {
        "id": model["id"],
        "name": mese.get("display_name") or model.get("name") or model["id"],
        "kind": mese.get("kind") or "cloud",
        "provider": {"id": provider.get("id"), "name": provider.get("name")} if provider else None,
        "node": ({"id": node.get("id"), "name": node.get("name"), "runtime": node.get("runtime"),
                  "host_id": node.get("host_id")} if node else None),
        "available": bool(mese.get("available", False)),
        "unavailable_reason": mese.get("unavailable_reason"),
        "load_state": mese.get("load_state"),
        "can_rank": bool(mese.get("can_rank", False)),
        "can_chair": bool(mese.get("can_chair", False)),
        "context_length": model.get("context_length"),
        "size_bytes": mese.get("size_bytes"),
        "price_per_mtok": price_per_mtok(model),
        "price_verified": mese.get("price_verified"),
        "tags": [t for t in (mese.get("tags") or []) if isinstance(t, str)],
        "lineage": {"name": lineage.get("name"), "version": lineage.get("version")} if lineage else None,
    }


class CatalogIndex:
    """An immutable view of one ``/v1/models`` response with alias resolution."""

    def __init__(self, v1_models: Iterable[Dict[str, Any]]) -> None:
        self._raw: Dict[str, Dict[str, Any]] = {}
        self._aliases: Dict[str, str] = {}
        self.models: List[CouncilModel] = []
        for model in v1_models:
            if not isinstance(model, dict) or not isinstance(model.get("id"), str) or model["id"] in self._raw:
                continue
            self._raw[model["id"]] = model
            self.models.append(project_model(model))
        for model_id, model in self._raw.items():
            for alias in (model.get("mese") or {}).get("aliases") or []:
                if isinstance(alias, str) and alias not in self._raw:
                    self._aliases.setdefault(alias, model_id)
        self._by_id: Dict[str, CouncilModel] = {m["id"]: m for m in self.models}

    def canonical(self, model_id: str) -> Optional[str]:
        """The catalog id for ``model_id`` (an id or an alias), or None when unknown."""
        if model_id in self._raw:
            return model_id
        return self._aliases.get(model_id)

    def get(self, model_id: str) -> Optional[CouncilModel]:
        canonical = self.canonical(model_id)
        return self._by_id.get(canonical) if canonical else None

    def info(self, model_id: str) -> MemberInfo:
        """Member metadata; ids missing from the catalog get a conservative fallback (treated as cloud)."""
        canonical = self.canonical(model_id)
        if canonical is None:
            prefix, _, rest = model_id.partition("/")
            return MemberInfo(id=model_id, display_name=LEGACY_DISPLAY_NAMES.get(model_id, rest or model_id),
                              kind="cloud", provider_id=prefix or None, node_id=None, upstream_model=rest or model_id,
                              can_rank=not model_id.startswith("ft/"), can_chair=False, context_length=None,
                              lineage=None, in_catalog=False)
        raw = self._raw[canonical]
        projected = self._by_id[canonical]
        mese = raw.get("mese") if isinstance(raw.get("mese"), dict) else {}
        lineage = mese.get("lineage") if isinstance(mese.get("lineage"), dict) else None
        return MemberInfo(
            id=canonical, display_name=projected["name"], kind=projected["kind"],
            provider_id=(projected["provider"] or {}).get("id"), node_id=(projected["node"] or {}).get("id"),
            upstream_model=str(mese.get("upstream_model") or canonical.partition("/")[2]),
            can_rank=projected["can_rank"], can_chair=projected["can_chair"],
            context_length=projected["context_length"] if isinstance(projected["context_length"], int) else None,
            lineage=({"name": lineage.get("name"), "version": lineage.get("version"),
                      "training_run_id": lineage.get("training_run_id")} if lineage else None),
        )

    def is_available(self, model_id: str) -> bool:
        model = self.get(model_id)
        return bool(model and model["available"])


def normalize_council(members: List[str], chairman: str, catalog: CatalogIndex) -> Dict[str, Any]:
    """Validates a council against the catalog and returns ``{"members", "chairman"}`` with canonical ids.

    Raises :class:`CouncilValidationError` (``unknown_models`` or ``chairman_cannot_chair``).
    """
    requested = list(dict.fromkeys(list(members) + [chairman]))
    unknown = [model_id for model_id in requested if catalog.canonical(model_id) is None]
    if unknown:
        raise CouncilValidationError("unknown_models", "Some models are not in the Mese catalog: " + ", ".join(unknown),
                                     bad_ids=unknown)
    canonical_members = list(dict.fromkeys(catalog.canonical(m) for m in members))
    canonical_chair = catalog.canonical(chairman)
    assert canonical_chair is not None  # checked above
    chair = catalog.get(canonical_chair)
    if not chair or not chair["can_chair"]:
        raise CouncilValidationError("chairman_cannot_chair", f"{canonical_chair} cannot chair a council",
                                     model=canonical_chair)
    return {"members": canonical_members, "chairman": canonical_chair}


def council_has_cloud(members: Iterable[str], chairman: str, catalog: CatalogIndex) -> bool:
    """True when any member or the chairman is a cloud model (unknown ids count as cloud)."""
    return any(not catalog.info(model_id).is_local for model_id in [*members, chairman])

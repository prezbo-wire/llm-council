"""Built-in preset selectors and catalog projection/validation (§I.5) on hand-made catalogs."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

import pytest

from backend.catalog import CatalogIndex, CouncilValidationError, normalize_council, project_model, usd_per_mtok
from backend.presets import resolve_builtin
from conftest import load_fixture_json

BASE = load_fixture_json("v1_models.json")["data"]


def _model(model_id: str, kind: str, *, available: bool = True, tags: Optional[List[str]] = None, can_rank: bool = True,
           can_chair: bool = True, ctx: Optional[int] = 32768, size: Optional[int] = None, load: Optional[str] = None,
           price: tuple = ("0", "0")) -> Dict[str, Any]:
    entry = copy.deepcopy(BASE[0] if kind == "cloud" else BASE[7])
    entry.update(id=model_id, name=model_id, context_length=ctx, pricing={"prompt": price[0], "completion": price[1]})
    entry["mese"].update(kind=kind, display_name=model_id, available=available, tags=tags or [], can_rank=can_rank,
                         can_chair=can_chair, size_bytes=size, load_state=load if kind != "cloud" else None, aliases=[])
    return entry


def test_all_local_orders_loaded_first_then_size_and_caps_at_four():
    catalog = CatalogIndex([
        _model("n/small-loaded", "local", size=1, load="loaded"),
        _model("n/big-unloaded", "local", size=50, load="unloaded"),
        _model("n/mid-loaded", "local", size=10, load="loaded"),
        _model("n/huge-unloaded", "local", size=90, load="unloaded"),
        _model("n/xl-unloaded", "local", size=70, load="unloaded"),
        _model("n/down", "local", size=999, load="loaded", available=False),
        _model("ft/no-rank", "custom", size=500, load="loaded", can_rank=False, can_chair=False),
        _model("n/chair", "local", size=5, load="unloaded", ctx=200000, can_rank=False),
    ])
    preset = resolve_builtin("builtin:all-local", catalog)
    assert preset["members"] == ["n/mid-loaded", "n/small-loaded", "n/huge-unloaded", "n/xl-unloaded"]
    assert preset["chairman"] == "n/chair"  # largest context among available local can_chair models


def test_local_chair_ties_broken_by_size_and_none_without_locals():
    catalog = CatalogIndex([_model("n/a", "local", ctx=8192, size=1), _model("n/b", "local", ctx=8192, size=2),
                            _model("c/x", "cloud", tags=["frontier"])])
    assert resolve_builtin("builtin:all-local", catalog)["chairman"] == "n/b"
    cloud_only = CatalogIndex([_model("c/x", "cloud", tags=["frontier"])])
    empty = resolve_builtin("builtin:all-local", cloud_only)
    assert (empty["members"], empty["chairman"]) == ([], None)


def test_frontier_and_hybrid_selection():
    catalog = CatalogIndex([
        _model("c/pricey", "cloud", tags=["frontier", "default_chair"], price=("0.00001", "0.00005")),
        _model("c/cheap", "cloud", tags=["frontier"], price=("0.0000001", "0.0000002")),
        _model("c/mid", "cloud", tags=["frontier"], price=("0.000001", "0.000002")),
        _model("c/unpriced", "cloud", tags=["frontier"], price=("n/a", "n/a")),
        _model("c/off", "cloud", tags=["frontier"], available=False, price=("0", "0")),
        _model("c/fast", "cloud", tags=["fast"], price=("0", "0")),
        *[_model(f"c/f{i}", "cloud", tags=["frontier"], price=("0.00002", "0.00002")) for i in range(3)],
        _model("n/local", "local", load="loaded", size=3),
    ])
    frontier = resolve_builtin("builtin:frontier-cloud", catalog)
    assert frontier["members"] == ["c/pricey", "c/cheap", "c/mid", "c/unpriced", "c/f0"]
    assert frontier["chairman"] == "c/pricey"
    hybrid = resolve_builtin("builtin:hybrid", catalog)
    assert hybrid["members"] == ["c/cheap", "c/mid", "n/local"] and hybrid["chairman"] == "c/pricey"


def test_cloud_chair_falls_back_when_default_chair_is_unavailable():
    catalog = CatalogIndex([_model("c/default", "cloud", tags=["frontier", "default_chair"], available=False),
                            _model("c/no-chair", "cloud", tags=["frontier"], can_chair=False),
                            _model("c/chair", "cloud", tags=["frontier"])])
    assert resolve_builtin("builtin:frontier-cloud", catalog)["chairman"] == "c/chair"


def test_projection_prices_are_exact_per_million():
    assert usd_per_mtok("0.000004") == 4.0 and usd_per_mtok("0.0000001") == 0.1
    assert usd_per_mtok("abc") is None and usd_per_mtok(None) is None and usd_per_mtok("-1") is None
    local = project_model(_model("n/x", "local"))
    assert local["price_per_mtok"] is None and local["node"]["runtime"] == "ollama"


def test_normalize_council_resolves_aliases_and_reports_unknowns():
    catalog = CatalogIndex(BASE)
    council = normalize_council(["anthropic/claude-opus-5.5", "spark/qwen3.8:27b"], "anthropic/claude-opus-5.5", catalog)
    assert council == {"members": ["anthropic/claude-opus-5-5", "spark/qwen3.8:27b"], "chairman": "anthropic/claude-opus-5-5"}
    with pytest.raises(CouncilValidationError) as unknown:
        normalize_council(["x/1", "spark/qwen3.8:27b", "x/1"], "x/2", catalog)
    assert unknown.value.detail()["bad_ids"] == ["x/1", "x/2"]
    with pytest.raises(CouncilValidationError) as chair:
        normalize_council(["spark/qwen3.8:27b"], "ft/legal-qwen-v3", catalog)
    assert chair.value.detail()["code"] == "chairman_cannot_chair"


def test_unknown_ids_get_a_legacy_safe_fallback():
    info = CatalogIndex(BASE).info("openai/gpt-5.1")
    assert (info.in_catalog, info.display_name, info.kind, info.is_local) == (False, "GPT-5.1", "cloud", False)

"""Residency (Mese ARCHITECTURE §G.6) in llm-council: the ``CouncilModel.residency`` projection, the resident-first
all-local and hybrid selections, and the plan proxy's ``chairman`` / ``turn`` (mock Mese only).

The API tests reuse the ``test_api`` fixtures; they live here only to keep ``test_api.py`` under 500 lines.
"""
from __future__ import annotations

import json
import random
from typing import Any, Dict, List, Optional

import pytest

from backend.catalog import CatalogIndex, project_model, project_residency
from backend.presets import resolve_builtin, select_local_chair, select_local_members
from conftest import load_fixture_json
from test_api import api, mock, settings  # noqa: F401 - fixtures
from test_presets import _model
from test_support import FT, GPT_OSS, OPUS, QWEN, VLLM

GB = 10**9
BASE = load_fixture_json("v1_models.json")["data"]


def _res(cls: str, configured: Optional[str] = None, load_s: Optional[float] = 20.0,
         tok_s: Optional[float] = None) -> Dict[str, Any]:
    """A wire ``V1Residency`` (``class`` is the effective class)."""
    return {"class": cls, "configured": configured or cls, "slots": 2, "est_bytes": 30 * GB, "est_load_s": load_s,
            "observed_tok_s": tok_s}


RESIDENT = _res("resident", load_s=0)
RELOADING = _res("resident", load_s=21.0)
ON_DEMAND = _res("on_demand")
EXCLUSIVE = _res("exclusive", load_s=48.3)
AUTO = _res("exclusive", "on_demand", load_s=48.3)  # "exclusive (auto)": too big to fit beside the residents
RESIDENT_AUTO = _res("exclusive", "resident")  # an over-committed resident set degrades to exclusive


def _local(model_id: str, residency: Optional[Dict[str, Any]], *, size: int, load: str = "loaded", **kwargs: Any):
    return _model(model_id, "local", size=size * GB, load=load, residency=residency, **kwargs)


# ── projection ───────────────────────────────────────────────────────────────


def test_projection_copies_only_the_four_whitelisted_fields():
    by_id = {m["id"]: project_model(m) for m in BASE}
    assert by_id[QWEN]["residency"] == {"effective": "resident", "configured": "resident", "est_load_s": 0,
                                        "observed_tok_s": 38.6}
    assert by_id[GPT_OSS]["residency"] == {"effective": "exclusive", "configured": "exclusive", "est_load_s": 51.5,
                                           "observed_tok_s": None}
    assert by_id[FT]["residency"]["effective"] == "on_demand"
    assert by_id[OPUS]["residency"] is None and by_id[VLLM]["residency"] is None  # cloud; a runtime Mese does not load
    wire = {**_res("exclusive", "on_demand", load_s=48.3, tok_s=4.4), "est_bytes": 81 * GB, "displaces": ["x/y"],
            "offloaded_bytes": 5, "base_url": "http://127.0.0.1:11434", "future_field": {"a": 1}}
    projected = project_residency(wire)
    assert projected == {"effective": "exclusive", "configured": "on_demand", "est_load_s": 48.3, "observed_tok_s": 4.4}
    assert list(projected) == ["effective", "configured", "est_load_s", "observed_tok_s"]


def test_projection_without_or_with_malformed_residency_is_none_or_cleaned():
    older = json.loads(json.dumps(BASE[7]))
    del older["mese"]["residency"]  # an older Mese
    assert project_model(older)["residency"] is None
    for bad in (None, "resident", [], {"class": "pinned"}, {"configured": "resident"}):
        assert project_residency(bad) is None, bad
    cleaned = project_residency({"class": "on_demand", "configured": "bogus", "est_load_s": -1, "observed_tok_s": True})
    assert cleaned == {"effective": "on_demand", "configured": "on_demand", "est_load_s": None, "observed_tok_s": None}
    assert project_residency({"class": "resident", "est_load_s": float("nan"), "observed_tok_s": "12"}) == \
        {"effective": "resident", "configured": "resident", "est_load_s": None, "observed_tok_s": None}


async def test_api_models_carry_the_projected_residency(api):
    models = {m["id"]: m for m in (await api.get("/api/models")).json()["models"]}
    assert models[QWEN]["residency"] == {"effective": "resident", "configured": "resident", "est_load_s": 0,
                                         "observed_tok_s": 38.6}
    assert models[OPUS]["residency"] is None and models[VLLM]["residency"] is None
    assert all(set(m["residency"]) == {"effective", "configured", "est_load_s", "observed_tok_s"}
               for m in models.values() if m["residency"] is not None)


# ── members ──────────────────────────────────────────────────────────────────


def test_residents_are_preferred_over_loaded_and_larger_models():
    catalog = CatalogIndex([
        _local("n/big-loaded", ON_DEMAND, size=90),
        _local("n/od-unloaded", ON_DEMAND, size=50, load="unloaded"),
        _local("n/res-reloading", RELOADING, size=5, load="unloaded"),
        _local("n/od-loaded", ON_DEMAND, size=10),
        _local("n/res-loaded", RESIDENT, size=1),
        _local("n/res-down", RESIDENT, size=2, available=False),
        _local("n/res-no-rank", RESIDENT, size=3, can_rank=False),
    ])
    assert select_local_members(catalog.models) == ["n/res-loaded", "n/res-reloading", "n/big-loaded", "n/od-loaded"]
    assert select_local_members(catalog.models, 2) == ["n/res-loaded", "n/res-reloading"]


def test_exclusive_models_are_excluded_unless_needed_to_reach_two():
    two_primary = CatalogIndex([_local("n/chair-120b", EXCLUSIVE, size=65, ctx=131072),
                                _local("n/auto", AUTO, size=80), _local("n/res", RESIDENT, size=20),
                                _local("n/od", ON_DEMAND, size=10, load="unloaded"),
                                _local("n/res-auto", RESIDENT_AUTO, size=30)])
    assert select_local_members(two_primary.models) == ["n/res", "n/od"]
    one_primary = CatalogIndex([_local("n/small-x", EXCLUSIVE, size=40), _local("n/auto", AUTO, size=80),
                                _local("n/res", RESIDENT, size=20)])
    assert select_local_members(one_primary.models) == ["n/res", "n/auto"]  # the largest exclusive model fills in
    only_exclusive = CatalogIndex([_local("n/a", EXCLUSIVE, size=40), _local("n/b", AUTO, size=80),
                                   _local("n/c", RESIDENT_AUTO, size=60)])
    assert select_local_members(only_exclusive.models) == ["n/b", "n/c"]
    assert select_local_members(CatalogIndex([_local("n/a", EXCLUSIVE, size=40)]).models) == ["n/a"]


def test_today_on_ollama_024_two_nemotrons_member_and_chair():
    """Design §B.7: no resident set; super is exclusive, nemotron:latest on_demand → both, super chairs."""
    catalog = CatalogIndex([
        _local("spark/nemotron-3-super:latest", EXCLUSIVE, size=87, ctx=131072, load="unloaded"),
        _local("spark/nemotron:latest", ON_DEMAND, size=43, ctx=131072, load="unloaded"),
    ])
    preset = resolve_builtin("builtin:all-local", catalog)
    assert preset["members"] == ["spark/nemotron:latest", "spark/nemotron-3-super:latest"]
    assert preset["chairman"] == "spark/nemotron-3-super:latest"


def test_hybrid_inherits_the_resident_first_rules():
    cloud = [_model("c/a", "cloud", tags=["frontier", "default_chair"], price=("0.000001", "0.000001")),
             _model("c/b", "cloud", tags=["frontier"], price=("0.000002", "0.000002"))]
    one_resident = CatalogIndex(cloud + [_local("n/big-od", ON_DEMAND, size=90, load="unloaded"),
                                         _local("n/res", RESIDENT, size=20), _local("n/x", EXCLUSIVE, size=65)])
    hybrid = resolve_builtin("builtin:hybrid", one_resident)
    assert (hybrid["members"], hybrid["chairman"]) == (["c/a", "c/b", "n/res", "n/big-od"], "c/a")
    lonely = CatalogIndex(cloud + [_local("n/res", RESIDENT, size=20), _local("n/x", EXCLUSIVE, size=65)])
    assert resolve_builtin("builtin:hybrid", lonely)["members"] == ["c/a", "c/b", "n/res", "n/x"]


# ── chair ────────────────────────────────────────────────────────────────────


def _b6_catalog(**overrides: Dict[str, Any]) -> CatalogIndex:
    """Design §B.6: four small residents (qwen chairs by default) and an exclusive gpt-oss:120b."""
    rows = {
        "spark/qwen3.6:35b-a3b-q4_K_M": dict(residency=RESIDENT, size=24, ctx=65536),
        "spark/gemma4:26b-a4b-it-q4_K_M": dict(residency=RESIDENT, size=18, ctx=32768),
        "spark/nemotron-3.5-bolt:latest": dict(residency=RESIDENT, size=25, ctx=32768),
        "spark/gpt-oss:20b": dict(residency=RESIDENT, size=14, ctx=32768),
        "spark/gpt-oss:120b": dict(residency=EXCLUSIVE, size=65, ctx=131072, load="unloaded"),
    }
    for model_id, change in overrides.items():
        rows[model_id] = {**rows[model_id], **change}
    return CatalogIndex([_local(model_id, row.pop("residency"), **row) for model_id, row in rows.items()])


def test_the_chair_is_the_resident_with_the_largest_context():
    catalog = _b6_catalog()
    preset = resolve_builtin("builtin:all-local", catalog)
    assert preset["chairman"] == "spark/qwen3.6:35b-a3b-q4_K_M"  # not the exclusive 131k-context gpt-oss:120b
    assert preset["members"] == ["spark/nemotron-3.5-bolt:latest", "spark/qwen3.6:35b-a3b-q4_K_M",
                                 "spark/gemma4:26b-a4b-it-q4_K_M", "spark/gpt-oss:20b"]
    tie = _b6_catalog(**{"spark/qwen3.6:35b-a3b-q4_K_M": dict(ctx=32768)})
    assert select_local_chair(tie.models) == "spark/nemotron-3.5-bolt:latest"  # ties: the largest resident
    no_chair = _b6_catalog(**{"spark/qwen3.6:35b-a3b-q4_K_M": dict(can_chair=False, ctx=262144)})
    assert select_local_chair(no_chair.models) == "spark/nemotron-3.5-bolt:latest"
    down = _b6_catalog(**{"spark/qwen3.6:35b-a3b-q4_K_M": dict(available=False)})
    assert select_local_chair(down.models) == "spark/nemotron-3.5-bolt:latest"


def test_without_a_resident_chair_todays_rule_applies_to_every_local_chair():
    demoted = {model_id: dict(residency=RESIDENT_AUTO) for model_id in
               ("spark/qwen3.6:35b-a3b-q4_K_M", "spark/gemma4:26b-a4b-it-q4_K_M", "spark/nemotron-3.5-bolt:latest",
                "spark/gpt-oss:20b")}
    assert select_local_chair(_b6_catalog(**demoted).models) == "spark/gpt-oss:120b"  # exclusive chairs: largest context
    no_resident_chair = {model_id: dict(can_chair=False) for model_id in demoted}
    assert select_local_chair(_b6_catalog(**no_resident_chair).models) == "spark/gpt-oss:120b"
    cloud_only = CatalogIndex([_model("c/x", "cloud", tags=["frontier"])])
    assert select_local_chair(cloud_only.models) is None


# ── a catalog without residency ──────────────────────────────────────────────


def _v1_members(models: List[Dict[str, Any]], limit: int) -> List[str]:
    """The pre-residency rule, verbatim: available local rankers, loaded first, then size descending."""
    candidates = [m for m in models if m["available"] and m["kind"] in ("local", "custom") and m["can_rank"]]
    candidates.sort(key=lambda m: (m["load_state"] != "loaded", -(m["size_bytes"] or 0)))
    return [m["id"] for m in candidates[:limit]]


def _v1_chair(models: List[Dict[str, Any]]) -> Optional[str]:
    candidates = [m for m in models if m["available"] and m["kind"] in ("local", "custom") and m["can_chair"]]
    if not candidates:
        return None
    return max(candidates, key=lambda m: (m["context_length"] or 0, m["size_bytes"] or 0))["id"]


def _random_catalog(rng: random.Random, residency_mode: str) -> List[Dict[str, Any]]:
    entries = []
    for i in range(rng.randint(0, 9)):
        kind = rng.choice(["local", "local", "custom", "cloud"])
        entry = _model(f"n/m{i}", kind, available=rng.random() > 0.2, can_rank=rng.random() > 0.2,
                       can_chair=rng.random() > 0.3, ctx=rng.choice([None, 8192, 32768, 131072]),
                       size=rng.choice([None, 1 * GB, 5 * GB, 18 * GB, 70 * GB]),
                       load=rng.choice([None, "loaded", "unloaded", "unknown"]),
                       residency=_res("on_demand") if residency_mode == "on_demand" and kind != "cloud" else None)
        if residency_mode == "absent":
            del entry["mese"]["residency"]
        entries.append(entry)
    return entries


@pytest.mark.parametrize("residency_mode", ["absent", "null", "on_demand"])
def test_a_catalog_without_residency_gives_exactly_todays_selections(residency_mode):
    rng = random.Random(f"wp-c-{residency_mode}")
    for _ in range(300):
        models = CatalogIndex(_random_catalog(rng, residency_mode)).models
        assert select_local_members(models) == _v1_members(models, 4)
        assert select_local_members(models, 2) == _v1_members(models, 2)
        assert select_local_chair(models) == _v1_chair(models)


# ── plan proxy ───────────────────────────────────────────────────────────────


async def test_plan_proxy_sends_the_chairman_and_passes_the_turn_through(api, mock):
    exclusive_plan = load_fixture_json("v1_plan_exclusive.json")
    mock.plan = exclusive_plan
    response = await api.post("/api/council/plan", json={"members": [QWEN, VLLM], "chairman": GPT_OSS})
    assert response.status_code == 200 and response.json() == exclusive_plan
    turn = response.json()["turn"]
    assert (turn["loads"], turn["load_s"], turn["restore_s"]) == (1, 48.3, 52.3)
    assert turn["stages"] == [{"stage": "stage3", "loads": [GPT_OSS], "evicts": exclusive_plan["turn"]["stages"][0]["evicts"],
                               "load_s": 48.3}]
    # §I.1 `{models: [...members], chairman}`: Mese plans every id in `models` as a Stage-1/2 member, so a chair
    # that is not a member stays out of `models` (else the turn loads it in Stage 1, 2 and 3).
    sent = [json.loads(r.content) for r in mock.requests if r.url.path == "/v1/mese/plan"]
    assert sent == [{"models": [QWEN, VLLM], "chairman": GPT_OSS}]
    member_chair = await api.post("/api/council/plan", json={"members": [QWEN, VLLM], "chairman": QWEN})
    assert member_chair.status_code == 200
    assert json.loads(mock.requests[-1].content) == {"models": [QWEN, VLLM], "chairman": QWEN}
    listed_chair = await api.post("/api/council/plan", json={"members": [QWEN, GPT_OSS, VLLM], "chairman": GPT_OSS})
    assert listed_chair.status_code == 200
    assert json.loads(mock.requests[-1].content) == {"models": [QWEN, GPT_OSS, VLLM], "chairman": GPT_OSS}  # no duplicate

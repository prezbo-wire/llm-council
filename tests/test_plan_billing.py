"""ChatGPT-plan models (``chatgpt/*``, Mese ``billing: chatgpt_plan``) in llm-council: the ``billing`` projection,
the never-switch-billing rule (aliases, Last used, built-in presets) and ``plan_limit_exceeded`` through a council
run. Mock Mese only; nothing here reaches OpenAI or ChatGPT.

Plan streams come from conftest's ``build_mese_sse``, which knows the ``chatgpt`` provider (cloud, ``cost_source``
'plan'); only the resolved provider fields are added here. The API tests reuse the ``test_api`` fixtures, the runner
tests ``test_council``'s.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Iterable, List, Optional

import httpx
import pytest

from backend.catalog import (CatalogIndex, CouncilValidationError, is_plan_billed, normalize_council,
                             project_billing, project_model)
from backend.mese_client import parse_error_body
from backend.council.title import choose_title_model
from backend.presets import resolve_builtin, resolve_last_used
from conftest import MeseMock, build_mese_sse, load_fixture_json
from test_api import api, mock, settings  # noqa: F401 - fixtures
from test_council import _persisted, _run, store  # noqa: F401 - fixtures
from test_support import FT, GPT, OPUS, QWEN, RANKING_TEXT, chat_calls, patch_model, route_member

PLAN = "chatgpt/gpt-6.1-sol"
BASE = load_fixture_json("v1_models.json")["data"]
LIMIT = "ChatGPT plan usage limit reached; Mese pauses chatgpt/* until it is resumed (request id req_plan)"


def _wire(*, plan_available: bool = True, aliases: Optional[Dict[str, List[str]]] = None,
          drop: Iterable[str] = (), tags: Optional[List[str]] = None, billing: bool = True) -> List[Dict[str, Any]]:
    """The fixture ``/v1/models`` with the plan model connected (or still paused), extra aliases, dropped ids,
    plan-model tags, and optionally without ``billing`` (an older Mese)."""
    data = copy.deepcopy(BASE)
    for entry in data:
        mese = entry["mese"]
        if entry["id"] == PLAN and plan_available:
            mese.update(available=True, unavailable_reason=None)
        if entry["id"] == PLAN and tags is not None:
            mese["tags"] = tags
        mese["aliases"] = (aliases or {}).get(entry["id"], mese["aliases"])
        if not billing:
            mese.pop("billing", None)
    return [entry for entry in data if entry["id"] not in set(drop)]


def plan_sse(content: str, **kwargs: Any) -> bytes:
    """A Mese stream for the plan model: ``cost_usd`` 0 with ``cost_source`` 'plan' (Mese spec §7.2)."""
    stream = build_mese_sse(PLAN, content, cost_usd=0.0, **kwargs)
    assert b'"cost_source":"plan"' in stream and b'"provider_id":"chatgpt"' in stream
    return stream


def route_plan(mock: MeseMock, **stages: Any) -> None:
    """Answers the plan model per ``mese.stage``: a text, ``build_mese_sse`` kwargs, or an ``httpx.Response``."""
    defaults = {"stage1": "The plan model says hello", "stage2": RANKING_TEXT, "stage3": "Plan synthesis"}

    def respond(request: httpx.Request) -> httpx.Response:
        spec = {**defaults, **stages}.get(json.loads(request.content)["mese"]["stage"], "Plan answer")
        if isinstance(spec, httpx.Response):
            return spec
        kwargs = dict(spec) if isinstance(spec, dict) else {"content": spec}
        body = plan_sse(kwargs.pop("content", ""), **kwargs)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    mock.route_chat(PLAN, respond)


def limit_response(retryable: Optional[bool] = None) -> httpx.Response:
    """Mese's pre-stream 429 ``plan_limit_exceeded`` (admission refusal while paused, or the upstream limit)."""
    mese: Dict[str, Any] = {"request_id": "req_plan"}
    if retryable is not None:
        mese["retryable"] = retryable
    return httpx.Response(429, json={"error": {"message": LIMIT, "type": "rate_limit_error",
                                               "code": "plan_limit_exceeded", "param": None, "mese": mese}})


def _connect_plan(mock: MeseMock) -> None:
    patch_model(mock, PLAN, available=True, unavailable_reason=None)


# ── projection ───────────────────────────────────────────────────────────────


def test_projection_copies_billing_and_drops_unknown_values():
    by_id = {m["id"]: project_model(m) for m in BASE}
    assert [by_id[i]["billing"] for i in (OPUS, GPT, QWEN, FT, PLAN)] == \
        ["api_key", "api_key", "local", "local", "chatgpt_plan"]
    plan = by_id[PLAN]
    assert (plan["kind"], plan["residency"], plan["price_per_mtok"]) == ("cloud", None, {"input": 0.0, "output": 0.0})
    assert (plan["available"], plan["unavailable_reason"]) == (False, "plan_limited")  # greyed out, still listed
    assert [project_billing(v) for v in ("api_key", "chatgpt_plan", "local", "prepaid", None, 1)] == \
        ["api_key", "chatgpt_plan", "local", None, None, None]
    older = copy.deepcopy(BASE[0])
    del older["mese"]["billing"]
    assert project_model(older)["billing"] is None


def test_member_info_and_snapshot_carry_billing():
    catalog = CatalogIndex(BASE)
    plan, opus = catalog.info(PLAN), catalog.info(OPUS)
    assert (plan.billing, plan.is_plan, opus.billing, opus.is_plan) == ("chatgpt_plan", True, "api_key", False)
    assert plan.snapshot()["billing"] == "chatgpt_plan" and plan.snapshot(chairman=True)["billing"] == "chatgpt_plan"
    gone = catalog.info("chatgpt/gpt-7")  # left the catalog: the id alone says plan
    assert (gone.in_catalog, gone.billing, gone.is_plan) == (False, "chatgpt_plan", True)
    assert catalog.info("openai/gone").billing is None and not catalog.info("openai/gone").is_plan
    assert is_plan_billed("chatgpt/x", None) and not is_plan_billed("chatgpt/x", "api_key")  # Mese's billing wins
    assert not is_plan_billed("openai/gpt-6.1-sol", None)


# ── never switch billing ─────────────────────────────────────────────────────


def test_aliases_never_cross_billing():
    to_api = CatalogIndex(_wire(drop=[PLAN], aliases={GPT: [PLAN, "gpt-sol"]}))
    assert to_api.canonical(PLAN) is None and to_api.get(PLAN) is None
    assert to_api.canonical("gpt-sol") == GPT  # a same-billing alias still resolves
    to_plan = CatalogIndex(_wire(drop=[GPT], aliases={PLAN: [GPT, "chatgpt/sol"]}))
    assert to_plan.canonical(GPT) is None and to_plan.canonical("chatgpt/sol") == PLAN
    with pytest.raises(CouncilValidationError) as exc:
        normalize_council([PLAN, QWEN], OPUS, to_api)
    assert (exc.value.code, exc.value.bad_ids) == ("unknown_models", [PLAN])
    with pytest.raises(CouncilValidationError) as exc:
        normalize_council([QWEN], GPT, to_plan)
    assert exc.value.bad_ids == [GPT]


def test_last_used_keeps_the_billing_it_was_sent_with():
    record = {"members": [PLAN, QWEN], "chairman": PLAN, "preset_id": None, "conversation_id": "c",
              "used_at": "2026-11-17T12:00:00Z"}
    paused = resolve_last_used(record, CatalogIndex(_wire(plan_available=False)))
    assert (paused["members"], paused["chairman"], paused["unavailable"]) == ([PLAN, QWEN], PLAN, [PLAN])
    gone = resolve_last_used(record, CatalogIndex(_wire(drop=[PLAN], aliases={GPT: [PLAN]})))
    assert (gone["members"], gone["chairman"], gone["unavailable"]) == ([QWEN], None, [PLAN])
    reverse = dict(record, members=[GPT, QWEN], chairman=GPT)
    api_gone = resolve_last_used(reverse, CatalogIndex(_wire(drop=[GPT], aliases={PLAN: [GPT]})))
    assert (api_gone["members"], api_gone["chairman"], api_gone["unavailable"]) == ([QWEN], None, [GPT])


def test_a_plan_council_is_never_titled_by_an_api_key_model():
    """TITLE_MODEL (API key) needs an API-key cloud model in the council; plan and local councils use the chairman."""
    catalog = CatalogIndex(_wire())
    assert choose_title_model(OPUS, [PLAN, QWEN], PLAN, catalog) == PLAN        # plan + local: no API charge for a title
    assert choose_title_model(OPUS, [PLAN], QWEN, catalog) == QWEN
    assert choose_title_model(OPUS, [PLAN, GPT], PLAN, catalog) == OPUS         # an API-key member: TITLE_MODEL as before
    assert choose_title_model(QWEN, [PLAN], PLAN, catalog) == QWEN              # a local TITLE_MODEL is always fine
    assert choose_title_model(PLAN, [GPT], OPUS, catalog) == PLAN               # a plan TITLE_MODEL with a cloud council
    assert choose_title_model(PLAN, [QWEN], QWEN, catalog) == QWEN              # never sends an all-local question out
    assert choose_title_model(None, [PLAN], PLAN, catalog) == PLAN


@pytest.mark.parametrize("billing", [True, False], ids=["billing", "older-mese"])
def test_builtins_never_pick_a_plan_model(billing):
    """Decision: frontier-cloud and hybrid select API-key models only, even with the plan model connected, tagged
    frontier and default_chair, and cheapest ($0); without ``billing`` the ``chatgpt/`` prefix decides."""
    catalog = CatalogIndex(_wire(tags=["frontier", "plan", "default_chair"], billing=billing))
    without_plan = CatalogIndex(_wire(drop=[PLAN], billing=billing))
    assert catalog.is_available(PLAN)
    for preset_id in ("builtin:frontier-cloud", "builtin:hybrid", "builtin:all-local"):
        preset, baseline = resolve_builtin(preset_id, catalog), resolve_builtin(preset_id, without_plan)
        assert (preset["members"], preset["chairman"]) == (baseline["members"], baseline["chairman"]), preset_id
        assert PLAN not in preset["members"] and preset["chairman"] != PLAN, preset_id
    assert resolve_builtin("builtin:frontier-cloud", catalog)["chairman"] == OPUS
    assert GPT in resolve_builtin("builtin:hybrid", catalog)["members"]  # the API-key twin stays API-key billed
    plan_only = CatalogIndex(_wire(tags=["frontier"], drop=[m["id"] for m in BASE if m["mese"]["kind"] == "cloud"
                                                          and m["id"] != PLAN]))
    frontier = resolve_builtin("builtin:frontier-cloud", plan_only)
    assert (frontier["members"], frontier["chairman"]) == ([], None)  # empty rather than billed to the plan


# ── REST API ─────────────────────────────────────────────────────────────────


async def test_models_and_presets_api_with_a_connected_plan(api, mock):
    _connect_plan(mock)
    models = {m["id"]: m for m in (await api.get("/api/models")).json()["models"]}
    assert (models[PLAN]["billing"], models[OPUS]["billing"], models[QWEN]["billing"]) == \
        ("chatgpt_plan", "api_key", "local")
    presets = {p["id"]: p for p in (await api.get("/api/presets")).json()["presets"]}
    for preset_id in ("builtin:frontier-cloud", "builtin:hybrid"):
        assert PLAN not in presets[preset_id]["members"] + [presets[preset_id]["chairman"]], preset_id
    assert "ChatGPT plan" in presets["builtin:frontier-cloud"]["description"]


# ── runs ─────────────────────────────────────────────────────────────────────


async def test_a_plan_member_costs_zero_and_is_labelled_plan(store):
    mock = MeseMock()
    _connect_plan(mock)
    route_plan(mock)
    route_member(mock, OPUS, cost_usd=0.01)
    result, log = await _run(mock, store, {"members": [PLAN, OPUS], "chairman": OPUS}, first=False)
    started = log.one("council_started")
    assert [m["billing"] for m in started["members"]] == ["chatgpt_plan", "api_key"]
    assert started["chairman"]["billing"] == "api_key"
    assert log.one("member_complete", memberId=PLAN)["costUsd"] == 0.0
    assert log.one("stage2_model_complete", memberId=PLAN)["ranking"]  # the plan model ranks like any member
    stored = _persisted(store)
    assert [m["billing"] for m in stored["council_snapshot"]["members"]] == ["chatgpt_plan", "api_key"]
    assert stored["usage"]["cost_usd"] == pytest.approx(0.01 * 3)  # Opus stage 1, 2 and 3; the plan adds $0
    assert log.one("run_summary")["costUsd"] == stored["usage"]["cost_usd"] and result.status == "complete"


async def test_plan_limit_before_the_stream_fails_only_that_member(store):
    mock = MeseMock()
    route_plan(mock, stage1=limit_response())
    route_member(mock, OPUS)
    route_member(mock, QWEN)
    result, log = await _run(mock, store, {"members": [PLAN, OPUS, QWEN], "chairman": OPUS}, first=False)
    error = log.one("member_error", memberId=PLAN)
    assert (error["code"], error["message"], error["retryable"]) == ("plan_limit_exceeded", LIMIT, False)
    assert result.failures == [{"stage": "1", "model": PLAN, "code": "plan_limit_exceeded", "message": LIMIT}]
    assert result.status == "partial"
    calls = chat_calls(mock)
    assert [c["stage"] for c in calls if c["model"] == PLAN] == ["stage1"]  # no retry, no ranking, no chair call
    assert {c["model"] for c in calls} == {PLAN, OPUS, QWEN}  # nothing stood in for the plan model


async def test_plan_limit_after_text_discards_the_partial_answer(store):
    mock = MeseMock()
    cut = {"content": "Half an answ", "finish_reason": "error",
           "error": {"code": "plan_limit_exceeded", "message": LIMIT, "retryable": True}}
    route_plan(mock, stage1=cut)
    route_member(mock, OPUS)
    route_member(mock, QWEN)
    result, log = await _run(mock, store, {"members": [OPUS, PLAN, QWEN], "chairman": OPUS}, first=False)
    error = log.one("member_error", memberId=PLAN)
    assert (error["code"], error["retryable"]) == ("plan_limit_exceeded", False)  # never retried
    assert [e["model"] for e in log.one("stage1_complete")["data"]] == [OPUS, QWEN]
    assert PLAN not in log.one("stage2_complete")["metadata"]["label_to_model"].values()
    assert [e["model"] for e in _persisted(store)["stage1"]] == [OPUS, QWEN]
    assert result.stage3["error"] is None


async def test_a_plan_chairman_at_its_limit_is_a_final_error_without_fallback(store):
    mock = MeseMock()
    route_plan(mock, stage3=limit_response(retryable=False))
    route_member(mock, OPUS)
    route_member(mock, QWEN)
    result, log = await _run(mock, store, {"members": [OPUS, QWEN, PLAN], "chairman": PLAN}, first=False)
    assert log.one("final_error") == {"type": "final_error", "code": "plan_limit_exceeded", "message": LIMIT}
    assert result.stage3 == {"model": PLAN, "response": "", "cost_usd": None,
                             "error": {"code": "plan_limit_exceeded", "message": LIMIT}}
    assert [c["model"] for c in chat_calls(mock) if c["stage"] == "stage3"] == [PLAN]
    assert _persisted(store)["council_snapshot"]["chairman"]["billing"] == "chatgpt_plan"


def test_plan_limit_is_never_retryable_whatever_the_status_says():
    def body(code: str, mese: Dict[str, Any]) -> bytes:
        return json.dumps({"error": {"message": "m", "code": code, "mese": mese}}).encode()

    assert parse_error_body(body("plan_limit_exceeded", {}), 429) == ("plan_limit_exceeded", "m", False)
    assert parse_error_body(body("plan_limit_exceeded", {"retryable": True}), 429)[2] is False
    assert parse_error_body(body("upstream_rate_limited", {}), 429)[2] is True  # an ordinary 429 still is

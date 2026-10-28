"""Shared helpers for the WP7 test modules (no tests here).

It lives under ``tests/test_*.py`` only because that is the path WP7 owns; ``conftest.py`` (WP0) stays
read-only. Everything talks to the WP0 ``MeseMock`` (``httpx.MockTransport``); nothing reaches a network.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from backend.catalog import CatalogIndex
from backend.config import Settings
from backend.council import RunnerDeps
from backend.mese_client import MeseClient
from backend.storage import ConversationStore
from conftest import TEST_MESE_TOKEN, TEST_MESE_URL, MeseMock, build_mese_sse

RANKING_TEXT = "Both answers were evaluated.\n\nFINAL RANKING:\n1. Response B\n2. Response A"

# Model ids from tests/fixtures/mese/v1_models.json
OPUS = "anthropic/claude-opus-5-5"
SONNET = "anthropic/claude-sonnet-5-5"
HAIKU = "anthropic/claude-haiku-5-5"
GPT = "openai/gpt-6.1-sol"
GEMINI = "google/gemini-4-argon"  # unavailable: no_credential
GROK = "xai/grok-4.7"  # unavailable: circuit_open
QWEN = "spark/qwen3.8:27b"  # local, loaded, ctx 65536
GPT_OSS = "spark/gpt-oss:120b"  # local, unloaded, ctx 131072
VLLM = "spark-vllm/Qwen/Qwen3.8-27B"  # local, loaded, ctx 32768
FT = "ft/legal-qwen-v3"  # custom, can_rank=False, can_chair=False


def stage_responder(model: str, contents: Dict[str, Any], **sse_kwargs: Any):
    """A chat responder that answers per ``mese.stage``.

    ``contents`` maps a stage ('stage1', 'stage2', 'stage3', 'title', or '*') to either a text or a dict of
    ``build_mese_sse`` kwargs (e.g. ``{"content": "x", "finish_reason": "error", "error": {...}}``).
    """

    def respond(request: httpx.Request) -> httpx.Response:
        stage = json.loads(request.content)["mese"]["stage"]
        spec = contents.get(stage, contents.get("*", f"{model} {stage} answer"))
        kwargs = dict(sse_kwargs)
        if isinstance(spec, dict):
            kwargs.update(spec)
            text = kwargs.pop("content", "")
        else:
            text = spec
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=build_mese_sse(model, text, **kwargs))

    return respond


def route_member(mock: MeseMock, model: str, *, stage1: Any = None, stage2: Any = RANKING_TEXT, stage3: Any = None,
                 title: Any = "Test Title", **sse_kwargs: Any) -> None:
    contents = {"stage1": stage1 if stage1 is not None else f"{model} says hello",
                "stage2": stage2, "stage3": stage3 if stage3 is not None else f"{model} synthesis", "title": title}
    mock.route_chat(model, stage_responder(model, contents, **sse_kwargs))


def route_http_error(mock: MeseMock, model: str, status: int, code: str, message: str, retryable: bool) -> None:
    body = {"error": {"message": message, "type": "server_error", "code": code, "param": None,
                      "mese": {"request_id": "req_test", "retryable": retryable}}}
    mock.route_chat(model, lambda _req: httpx.Response(status, json=body))


def chat_calls(mock: MeseMock) -> List[Dict[str, Any]]:
    """``[{model, stage, run_id, conversation_id, body}]`` for every chat request, in order."""
    return [{"model": b["model"], "stage": b["mese"]["stage"], "run_id": b["mese"]["run_id"],
             "conversation_id": b["mese"]["conversation_id"], "body": b} for b in mock.chat_bodies()]


def catalog_models(mock: MeseMock) -> List[Dict[str, Any]]:
    return mock.models["data"]


def patch_model(mock: MeseMock, model_id: str, **mese_fields: Any) -> None:
    """Edits one model of the mock catalog (top-level ``context_length`` or any ``mese`` field)."""
    data = copy.deepcopy(mock.models)
    for entry in data["data"]:
        if entry["id"] == model_id:
            if "context_length" in mese_fields:
                entry["context_length"] = mese_fields.pop("context_length")
            entry["mese"].update(mese_fields)
    mock.models = data


def make_client(mock: MeseMock, token: Optional[str] = TEST_MESE_TOKEN) -> MeseClient:
    return MeseClient(TEST_MESE_URL, token, transport=mock.transport)


def make_deps(mock: MeseMock, store: ConversationStore, *, title_model: Optional[str] = None,
              client: Optional[MeseClient] = None) -> RunnerDeps:
    return RunnerDeps(client=client or make_client(mock), store=store,
                      catalog=CatalogIndex(catalog_models(mock)), title_model=title_model)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: Dict[str, Any] = {"mese_url": TEST_MESE_URL, "mese_api_key": TEST_MESE_TOKEN,
                              "data_dir": str(tmp_path / "data"), "presets_path": str(tmp_path / "data" / "presets.json")}
    values.update(overrides)
    return Settings(**values)


class EventLog:
    """Collects emitted §I.6 events."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    async def __call__(self, event: Dict[str, Any]) -> None:
        # Serialize immediately, as the SSE writer would, so later mutation would be caught.
        self.events.append(json.loads(json.dumps(event)))

    @property
    def types(self) -> List[str]:
        return [e["type"] for e in self.events]

    def of(self, event_type: str, **match: Any) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type and all(e.get(k) == v for k, v in match.items())]

    def one(self, event_type: str, **match: Any) -> Dict[str, Any]:
        found = self.of(event_type, **match)
        assert len(found) == 1, (event_type, match, found)
        return found[0]

    def index(self, event_type: str) -> int:
        return self.types.index(event_type)


def parse_sse(text: str) -> List[Dict[str, Any]]:
    """Frontend-style parse of an SSE body: every ``data: `` line is one JSON event."""
    return [json.loads(line[6:]) for line in text.split("\n") if line.startswith("data: ")]

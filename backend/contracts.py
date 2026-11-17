"""Shared contract between llm-council and Mese (Mese ARCHITECTURE §I.3). Owned by WP0; read-only.

The types below are copied verbatim from §I.3. The MeseClient interface that WP7 implements in
``backend/mese_client.py`` is documented here so every module codes against the same signature::

    class MeseClient:
        def __init__(self, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None): ...
        async def aclose(self) -> None: ...
        async def list_models(self, force: bool = False) -> list[dict]: ...           # V1Model dicts, 10 s cache
        async def stream_chat(self, model: str, messages: list[dict], *, stage: str, run_id: str,
                              conversation_id: str, on_delta: OnDelta | None = None,
                              on_status: OnStatus | None = None) -> MemberResult: ...  # never raises for upstream errors
        async def plan(self, models: list[str]) -> dict: ...                          # V1PlanResponse
        async def warmup(self, models: list[str], run_id: str) -> None: ...            # best-effort, swallows errors
        async def status(self) -> dict: ...                                           # V1StatusResponse | {"error": ...}

Stream outcome → MemberResult (§I.2; ``ok`` is true only for finish_reason stop/length with answer text):

    finished with stop or length           ok=True
    stop or length with no answer text     ok=False, error_code="empty_response", retryable=False (cost/usage kept)
    content_filter                         ok=False, error_code="content_filtered", message "declined by provider (<category>)"
    error (finish_reason "error")          ok=False, error_code = Mese error.code
    HTTP error before the stream           ok=False, error_code from the error body
    connect failure                        ok=False, error_code="mese_unreachable"
    stream ended without a finish chunk    ok=False, error_code="upstream_error"

``stream_chat`` never returns None. Every call sends
``mese: {client: "llm-council", run_id, conversation_id, stage, events: true}``.
Golden Mese transcripts and their expected MemberResults live in ``tests/fixtures/mese/``.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Optional, TypedDict

MemberState = Literal["queued", "loading", "started", "retrying", "thinking"]
FailureStage = Literal["1", "2", "3", "title"]

@dataclass
class MemberResult:
    model: str
    ok: bool
    content: str = ""
    latency_ms: int = 0
    ttft_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    usage: Optional[dict] = None              # {prompt_tokens, completion_tokens, reasoning_tokens}
    finish_reason: Optional[str] = None
    error_code: Optional[str] = None          # Mese error.code | 'mese_unreachable' | 'content_filtered'
    error_message: Optional[str] = None
    retryable: bool = False
    resolved: Optional[dict] = None           # MeseResolved (snake_case)
    warnings: list[str] = field(default_factory=list)

OnDelta = Callable[[str], Awaitable[None]]
OnStatus = Callable[[dict], Awaitable[None]]   # {"state": MemberState, "position"?, "node_id"?, "attempt"?, "reason"?}

class Failure(TypedDict):
    stage: FailureStage
    model: str
    code: str
    message: str

class CouncilConfigDict(TypedDict):
    members: list[str]
    chairman: str

# SSE event type names emitted to the frontend (exhaustive; see §I.6)
SSE_EVENTS = (
    "council_started", "member_queued", "member_status", "member_streaming", "member_complete", "member_error",
    "stage1_complete", "stage2_start", "stage2_member_status", "stage2_model_complete", "stage2_member_error",
    "stage2_member_skipped", "stage2_skipped", "stage2_complete", "final_generating", "final_status",
    "final_streaming", "final_ready", "final_error", "stage3_complete", "title_complete", "run_summary",
    "complete", "error",
)

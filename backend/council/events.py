"""SSE event payloads sent to the frontend (Mese ARCHITECTURE §I.6).

Every event is ``data: {"type": …}`` with camelCase fields; ``memberId`` is the model id. Event type
names come from :data:`backend.contracts.SSE_EVENTS`; nothing else is ever emitted.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..catalog import MemberInfo
from ..contracts import SSE_EVENTS, Failure, MemberResult

Event = Dict[str, Any]

#: Optional ``on_status`` keys (snake_case from MeseClient) → camelCase event keys.
_STATUS_KEYS = (("position", "position"), ("node_id", "nodeId"), ("attempt", "attempt"), ("reason", "reason"))


def _event(event_type: str, **fields: Any) -> Event:
    assert event_type in SSE_EVENTS, event_type
    return {"type": event_type, **fields}


def to_sse(event: Event) -> str:
    """One SSE frame: ``data: <json>`` plus the blank line."""
    return f"data: {json.dumps(event)}\n\n"


def _usage(result: MemberResult) -> Optional[Dict[str, int]]:
    if not result.usage:
        return None
    return {"promptTokens": result.usage.get("prompt_tokens", 0),
            "completionTokens": result.usage.get("completion_tokens", 0),
            "reasoningTokens": result.usage.get("reasoning_tokens", 0)}


def _member_fields(info: MemberInfo) -> Dict[str, Any]:
    return {"memberId": info.id, "displayName": info.display_name, "kind": info.kind,
            "providerId": info.provider_id, "nodeId": info.node_id, "canRank": info.can_rank}


def council_started(run_id: str, members: List[MemberInfo], chairman: MemberInfo) -> Event:
    return _event("council_started", runId=run_id, members=[_member_fields(m) for m in members],
                  chairman={"modelId": chairman.id, "displayName": chairman.display_name, "kind": chairman.kind,
                            "providerId": chairman.provider_id, "nodeId": chairman.node_id})


def member_queued(info: MemberInfo) -> Event:
    return _event("member_queued", **_member_fields(info))


def member_status(member_id: str, status: Dict[str, Any]) -> Event:
    extra = {camel: status[snake] for snake, camel in _STATUS_KEYS if status.get(snake) is not None}
    return _event("member_status", memberId=member_id, state=status.get("state"), **extra)


def member_streaming(member_id: str, delta_text: str) -> Event:
    return _event("member_streaming", memberId=member_id, deltaText=delta_text)


def member_complete(result: MemberResult) -> Event:
    return _event("member_complete", memberId=result.model, response=result.content, latencyMs=result.latency_ms,
                  ttftMs=result.ttft_ms, costUsd=result.cost_usd, usage=_usage(result))


def member_error(result: MemberResult) -> Event:
    return _event("member_error", memberId=result.model, message=error_message(result),
                  code=error_code(result), retryable=result.retryable)


def stage1_complete(data: List[Dict[str, Any]]) -> Event:
    return _event("stage1_complete", data=data)


def stage2_start() -> Event:
    return _event("stage2_start")


def stage2_member_status(member_id: str, state: Optional[str]) -> Event:
    return _event("stage2_member_status", memberId=member_id, state=state)


def stage2_model_complete(member_id: str, parsed_ranking: List[str]) -> Event:
    return _event("stage2_model_complete", memberId=member_id, ranking=parsed_ranking)


def stage2_member_error(result: MemberResult) -> Event:
    return _event("stage2_member_error", memberId=result.model, code=error_code(result), message=error_message(result))


def stage2_member_skipped(member_id: str, reason: str) -> Event:
    return _event("stage2_member_skipped", memberId=member_id, reason=reason)


def stage2_skipped(reason: str) -> Event:
    return _event("stage2_skipped", reason=reason)


def stage2_complete(data: List[Dict[str, Any]], label_to_model: Dict[str, str],
                    aggregate_rankings: List[Dict[str, Any]], failures: List[Failure]) -> Event:
    return _event("stage2_complete", data=data, metadata={"label_to_model": label_to_model,
                                                          "aggregate_rankings": aggregate_rankings,
                                                          "failures": list(failures)})


def final_generating() -> Event:
    return _event("final_generating")


def final_status(status: Dict[str, Any]) -> Event:
    extra = {camel: status[snake] for snake, camel in _STATUS_KEYS[:2] if status.get(snake) is not None}
    return _event("final_status", state=status.get("state"), **extra)


def final_streaming(delta_text: str) -> Event:
    return _event("final_streaming", deltaText=delta_text)


def final_ready(result: MemberResult) -> Event:
    return _event("final_ready", content=result.content, latencyMs=result.latency_ms, costUsd=result.cost_usd,
                  model=result.model)


def final_error(code: str, message: str) -> Event:
    return _event("final_error", code=code, message=message)


def stage3_complete(data: Dict[str, Any]) -> Event:
    return _event("stage3_complete", data=data)


def title_complete(title: str) -> Event:
    return _event("title_complete", data={"title": title})


def run_summary(run_id: str, cost_usd: float, prompt_tokens: int, completion_tokens: int,
                failures: List[Failure], status: str) -> Event:
    return _event("run_summary", runId=run_id, costUsd=cost_usd,
                  tokens={"prompt": prompt_tokens, "completion": completion_tokens},
                  failures=list(failures), status=status)


def complete() -> Event:
    return _event("complete")


def error(message: str) -> Event:
    return _event("error", message=message)


def error_code(result: MemberResult) -> str:
    return result.error_code or "upstream_error"


def error_message(result: MemberResult) -> str:
    return result.error_message or error_code(result)

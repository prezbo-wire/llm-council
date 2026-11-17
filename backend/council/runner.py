"""The council run (Mese ARCHITECTURE §I.4): one function for streaming and non-streaming turns.

``run_council`` resolves the council against the catalog, then:

1. emits ``council_started`` and ``member_queued`` per member, and warms up a local chairman;
2. **Stage 1** streams every member in parallel (``member_status`` / ``member_streaming`` →
   ``member_complete`` or ``member_error``);
3. **Stage 2** ranks with the Stage-1 successes that ``can_rank`` (labels A…H in Stage-1 order),
   skipping rankers whose context is too small, or skips the stage with ``stage2_skipped``;
4. **Stage 3** streams the chairman; a failure gives ``final_error`` and ``stage3.error``, never fake text;
5. persists the assistant message atomically, **before** the title, so a client that disconnects while
   the title is generated cannot lose a finished (and possibly paid) turn;
6. generates the title (when the conversation still has the default one and no title call has finished),
   then updates the stored turn (title cost, title failure), the title and ``title_attempted`` in one
   atomic write;
7. emits ``run_summary`` and ``complete``.

A run that stops early still persists its assistant message, so a user message is never left without
one: ``status`` is ``cancelled`` (client gone) or ``error`` (unexpected exception), with the Stage-1 and
Stage-2 results that had finished, ``stage3.error`` explaining why there is no answer, and a
``cancelled`` failure for every Mese call that was still in flight.

Every Mese call carries ``run_id``, ``conversation_id`` and ``stage``. Every child task is tracked and
cancelled (closing its Mese stream) when the run ends or is cancelled.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Tuple

from ..catalog import CatalogIndex
from ..contracts import CouncilConfigDict, Failure, MemberResult, OnDelta, OnStatus
from ..storage import ConversationStore
from . import events as ev
from .prompts import build_chairman_prompt, build_ranking
from .ranking import calculate_aggregate_rankings, parse_ranking_from_text
from .tasks import TaskTracker
from .title import choose_title_model, title_from_result, title_messages

log = logging.getLogger(__name__)

Emit = Callable[[Dict[str, Any]], Awaitable[None]]

#: Tokens kept free for the ranker's answer in the Stage-2 context pre-check.
CONTEXT_RESERVE_TOKENS = 1024
#: Mese ``stage`` value → the stage key used in ``failures`` and ``usage.by_stage``.
STAGE_KEYS = {"stage1": "1", "stage2": "2", "stage3": "3", "title": "title"}
#: ``stage3.error`` of a turn that stopped early, by its ``status``.
UNFINISHED_ERRORS = {
    "cancelled": ("cancelled", "The run was cancelled before it finished (the client disconnected)."),
    "error": ("run_failed", "The council run failed: {detail}"),
}
CANCELLED_CALL_MESSAGE = "Cancelled before this call finished."


class ChatClient(Protocol):
    """The part of :class:`backend.mese_client.MeseClient` a run needs."""

    async def stream_chat(self, model: str, messages: List[Dict[str, Any]], *, stage: str, run_id: str,
                          conversation_id: str, on_delta: Optional[OnDelta] = None,
                          on_status: Optional[OnStatus] = None) -> MemberResult: ...

    async def warmup(self, models: List[str], run_id: str) -> None: ...


@dataclass
class RunnerDeps:
    client: ChatClient
    store: ConversationStore
    catalog: CatalogIndex
    title_model: Optional[str] = None


@dataclass
class TurnResult:
    run_id: str
    stage1: List[Dict[str, Any]]
    stage2: List[Dict[str, Any]]
    stage3: Dict[str, Any]
    metadata: Dict[str, Any]
    council_snapshot: Dict[str, Any]
    failures: List[Failure]
    usage: Dict[str, Any]
    status: str
    title: Optional[str] = None

    def to_message(self) -> Dict[str, Any]:
        """The persisted assistant message (§I.7)."""
        return {"role": "assistant", "stage1": self.stage1, "stage2": self.stage2, "stage3": self.stage3,
                "metadata": self.metadata, "council_snapshot": self.council_snapshot,
                "failures": self.failures, "usage": self.usage, "status": self.status}

    def to_response(self) -> Dict[str, Any]:
        """The body of ``POST /api/conversations/{id}/message``."""
        return {"stage1": self.stage1, "stage2": self.stage2, "stage3": self.stage3, "metadata": self.metadata,
                "failures": self.failures, "usage": self.usage}


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def fits_context(prompt: str, context_length: Optional[int]) -> bool:
    """The §I.4 pre-check: ``ceil(len(prompt)/4) <= context_length - 1024``; unknown contexts pass."""
    return context_length is None or estimate_tokens(prompt) <= context_length - CONTEXT_RESERVE_TOKENS


async def _discard(_event: Dict[str, Any]) -> None:
    return None


@dataclass
class UsageTotals:
    """Sums cost and tokens over every Mese call of a run, failed calls included (they may still cost)."""

    cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_stage: Dict[str, float] = field(default_factory=lambda: {"1": 0.0, "2": 0.0, "3": 0.0, "title": 0.0})

    def add(self, stage_key: str, result: MemberResult) -> None:
        if result.cost_usd:
            self.cost_usd += result.cost_usd
            self.by_stage[stage_key] = self.by_stage.get(stage_key, 0.0) + result.cost_usd
        if result.usage:
            self.prompt_tokens += int(result.usage.get("prompt_tokens") or 0)
            self.completion_tokens += int(result.usage.get("completion_tokens") or 0)

    def as_dict(self) -> Dict[str, Any]:
        return {"cost_usd": round(self.cost_usd, 10), "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "by_stage": {key: round(value, 10) for key, value in self.by_stage.items()}}


async def run_council(query: str, council: CouncilConfigDict, conversation_id: str, emit: Optional[Emit] = None, *,
                      deps: RunnerDeps, is_first_message: bool = False, run_id: Optional[str] = None) -> TurnResult:
    """Runs one council turn. ``emit`` receives every §I.6 event (the non-streaming route passes none).

    ``is_first_message`` asks for a title: the routes pass True while the conversation still has the
    default title and no title call has finished (``title_attempted``), so a first turn that was cancelled
    gets one on a later turn, but a failed title call is not repeated on every turn.
    """
    run = CouncilRun(query, council, conversation_id, emit or _discard, deps, is_first_message, run_id or new_run_id())
    return await run.execute()


class CouncilRun:
    """State of one council turn; use :func:`run_council`."""

    def __init__(self, query: str, council: CouncilConfigDict, conversation_id: str, emit: Emit, deps: RunnerDeps,
                 is_first_message: bool, run_id: str) -> None:
        self.query = query
        self.conversation_id = conversation_id
        self.emit = emit
        self.deps = deps
        self.is_first_message = is_first_message
        self.run_id = run_id
        self.members = [deps.catalog.info(model_id) for model_id in council["members"]]
        self.chairman = deps.catalog.info(council["chairman"])
        self.failures: List[Failure] = []
        self.usage = UsageTotals()
        self.tasks = TaskTracker()
        # Progress, kept so a run that stops early can still persist what it finished.
        self.partial_stage1: Dict[str, Dict[str, Any]] = {}  # model → Stage-1 entry, as members succeed
        self.partial_stage2: Dict[str, Dict[str, Any]] = {}  # ranker → Stage-2 entry, as rankers succeed
        self.label_to_model: Dict[str, str] = {}
        self.result_stage1: Optional[List[Dict[str, Any]]] = None
        self.result_stage2: Optional[Tuple[List[Dict[str, Any]], Dict[str, str], List[Dict[str, Any]]]] = None
        self.result_stage3: Optional[Dict[str, Any]] = None
        self.inflight: Dict[Tuple[str, str], None] = {}  # (stage key, model) of Mese calls not finished yet
        self.persisted = False

    async def execute(self) -> TurnResult:
        try:
            await self._start()
            self.result_stage1 = await self._stage1()
            self.result_stage2 = await self._stage2(self.result_stage1)
            self.result_stage3 = await self._stage3(self.result_stage1, self.result_stage2[0])
            result = self._turn(self._status())
            # The turn is finished (and maybe paid for): store it before the best-effort title call.
            self.deps.store.add_assistant_message(self.conversation_id, result.to_message())
            self.persisted = True
            if self.is_first_message:
                await self._title(result)
            await self.emit(ev.run_summary(self.run_id, result.usage["cost_usd"], self.usage.prompt_tokens,
                                           self.usage.completion_tokens, result.failures, result.status))
            await self.emit(ev.complete())
            return result
        except asyncio.CancelledError:
            self._persist_unfinished("cancelled")
            raise
        except Exception as exc:
            self._persist_unfinished("error", f"{type(exc).__name__}: {exc}"[:500])
            raise
        finally:
            await self.tasks.cancel_all()

    # ── the persisted turn ───────────────────────────────────────────────────

    def _status(self) -> str:
        return "partial" if self.failures else "complete"

    def _turn(self, status: str) -> TurnResult:
        """The turn as it stands: the stage results, or the partial ones of a run that stopped early."""
        if self.result_stage1 is not None:
            stage1 = self.result_stage1
        else:
            stage1 = [self.partial_stage1[m.id] for m in self.members if m.id in self.partial_stage1]
        if self.result_stage2 is not None:
            stage2, label_to_model, aggregate = self.result_stage2
        else:
            label_to_model = dict(self.label_to_model)
            stage2 = [self.partial_stage2[model] for model in label_to_model.values() if model in self.partial_stage2]
            aggregate = calculate_aggregate_rankings(stage2, label_to_model) if stage2 else []
        return TurnResult(
            run_id=self.run_id, stage1=stage1, stage2=stage2, stage3=self.result_stage3 or {},
            metadata={"label_to_model": label_to_model, "aggregate_rankings": aggregate},
            council_snapshot={"run_id": self.run_id, "members": [m.snapshot() for m in self.members],
                              "chairman": self.chairman.snapshot(chairman=True)},
            failures=list(self.failures), usage=self.usage.as_dict(), status=status,
        )

    def _persist_unfinished(self, status: str, detail: str = "") -> None:
        """Stores the assistant message of a run that stopped early (``cancelled`` / ``error``); never raises.

        It runs inside the ``except`` of a cancelled task, so it must not await.
        """
        if self.persisted:
            return
        self.persisted = True
        for stage_key, model in self.inflight:
            self.failures.append({"stage": stage_key, "model": model, "code": "cancelled",  # type: ignore[typeddict-item]
                                  "message": CANCELLED_CALL_MESSAGE})
        self.inflight.clear()
        if self.result_stage3 is None:
            code, template = UNFINISHED_ERRORS[status]
            self.result_stage3 = {"model": self.chairman.id, "response": "", "cost_usd": None,
                                  "error": {"code": code, "message": template.format(detail=detail)}}
        try:
            self.deps.store.add_assistant_message(self.conversation_id, self._turn(status).to_message())
        except Exception:  # noqa: BLE001 - the original cancellation/exception must still propagate
            log.exception("could not store the %s turn of run %s (conversation %s)", status, self.run_id,
                          self.conversation_id)

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _call(self, stage: str, model: str, messages: List[Dict[str, Any]], on_delta: Optional[OnDelta] = None,
                    on_status: Optional[OnStatus] = None) -> MemberResult:
        """One Mese call. A bug below this point becomes a member failure instead of killing the run.

        The call stays in ``inflight`` until it returns, so a cancelled run records it as ``cancelled``.
        """
        key = (STAGE_KEYS[stage], model)
        self.inflight[key] = None
        try:
            result = await self.deps.client.stream_chat(model, messages, stage=stage, run_id=self.run_id,
                                                        conversation_id=self.conversation_id,
                                                        on_delta=on_delta, on_status=on_status)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - isolate one member's failure
            log.exception("unexpected error calling %s (%s)", model, stage)
            result = MemberResult(model=model, ok=False, error_code="internal",
                                  error_message=f"{type(exc).__name__}: {exc}"[:500])
        self.inflight.pop(key, None)
        self.usage.add(STAGE_KEYS[stage], result)
        return result

    def _record_failure(self, stage_key: str, result: MemberResult) -> None:
        self.failures.append({"stage": stage_key, "model": result.model, "code": ev.error_code(result),
                              "message": ev.error_message(result)})  # type: ignore[typeddict-item]

    # ── start ────────────────────────────────────────────────────────────────

    async def _start(self) -> None:
        await self.emit(ev.council_started(self.run_id, self.members, self.chairman))
        for member in self.members:
            await self.emit(ev.member_queued(member))
        if self.chairman.is_local:  # best-effort; Mese warmup never evicts
            self.tasks.spawn(self.deps.client.warmup([self.chairman.id], self.run_id), name="warmup")

    # ── stage 1 ──────────────────────────────────────────────────────────────

    async def _stage1(self) -> List[Dict[str, Any]]:
        messages = [{"role": "user", "content": self.query}]
        tasks = [self.tasks.spawn(self._stage1_member(m.id, messages), name=f"stage1:{m.id}") for m in self.members]
        results = await asyncio.gather(*tasks)
        stage1 = [_stage1_entry(r) for r in results if r.ok]
        await self.emit(ev.stage1_complete(stage1))
        return stage1

    async def _stage1_member(self, model_id: str, messages: List[Dict[str, Any]]) -> MemberResult:
        async def on_delta(text: str) -> None:
            await self.emit(ev.member_streaming(model_id, text))

        async def on_status(status: Dict[str, Any]) -> None:
            await self.emit(ev.member_status(model_id, status))

        result = await self._call("stage1", model_id, messages, on_delta, on_status)
        if result.ok:
            self.partial_stage1[model_id] = _stage1_entry(result)
            await self.emit(ev.member_complete(result))
        else:
            self._record_failure("1", result)
            await self.emit(ev.member_error(result))
        return result

    # ── stage 2 ──────────────────────────────────────────────────────────────

    async def _stage2(self, stage1: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[Dict[str, Any]]]:
        if len(stage1) < 2:
            return await self._skip_stage2("fewer_than_two_responses")
        rankers = [self.deps.catalog.info(entry["model"]) for entry in stage1]
        rankers = [info for info in rankers if info.can_rank]
        if not rankers:
            return await self._skip_stage2("no_rankers")

        prompt, label_to_model = build_ranking(self.query, stage1)
        self.label_to_model = label_to_model
        await self.emit(ev.stage2_start())
        eligible = []
        for ranker in rankers:
            if fits_context(prompt, ranker.context_length):
                eligible.append(ranker.id)
            else:
                await self.emit(ev.stage2_member_skipped(ranker.id, "context"))

        messages = [{"role": "user", "content": prompt}]
        tasks = [self.tasks.spawn(self._stage2_member(model_id, messages), name=f"stage2:{model_id}")
                 for model_id in eligible]
        results = await asyncio.gather(*tasks)
        stage2 = [_stage2_entry(r) for r in results if r.ok]
        aggregate = calculate_aggregate_rankings(stage2, label_to_model)
        await self.emit(ev.stage2_complete(stage2, label_to_model, aggregate, self.failures))
        return stage2, label_to_model, aggregate

    async def _stage2_member(self, model_id: str, messages: List[Dict[str, Any]]) -> MemberResult:
        async def on_status(status: Dict[str, Any]) -> None:
            await self.emit(ev.stage2_member_status(model_id, status.get("state")))

        result = await self._call("stage2", model_id, messages, on_status=on_status)
        if result.ok:
            entry = self.partial_stage2[model_id] = _stage2_entry(result)
            await self.emit(ev.stage2_model_complete(model_id, entry["parsed_ranking"]))
        else:
            self._record_failure("2", result)
            await self.emit(ev.stage2_member_error(result))
        return result

    async def _skip_stage2(self, reason: str) -> Tuple[List[Dict[str, Any]], Dict[str, str], List[Dict[str, Any]]]:
        await self.emit(ev.stage2_skipped(reason))
        await self.emit(ev.stage2_complete([], {}, [], self.failures))
        return [], {}, []

    # ── stage 3 ──────────────────────────────────────────────────────────────

    async def _stage3(self, stage1: List[Dict[str, Any]], stage2: List[Dict[str, Any]]) -> Dict[str, Any]:
        chairman = self.chairman.id
        if not stage1:
            error = {"code": "no_responses", "message": "No council member produced a response to synthesize."}
            await self.emit(ev.final_error(**error))
            stage3: Dict[str, Any] = {"model": chairman, "response": "", "cost_usd": None, "error": error}
            await self.emit(ev.stage3_complete(stage3))
            return stage3

        await self.emit(ev.final_generating())

        async def on_delta(text: str) -> None:
            await self.emit(ev.final_streaming(text))

        async def on_status(status: Dict[str, Any]) -> None:
            await self.emit(ev.final_status(status))

        messages = [{"role": "user", "content": build_chairman_prompt(self.query, stage1, stage2)}]
        result = await self._call("stage3", chairman, messages, on_delta, on_status)
        if result.ok:
            stage3 = {"model": chairman, "response": result.content, "latency_ms": result.latency_ms,
                      "cost_usd": result.cost_usd, "error": None}
            await self.emit(ev.final_ready(result))
        else:
            self._record_failure("3", result)
            error = {"code": ev.error_code(result), "message": ev.error_message(result)}
            await self.emit(ev.final_error(**error))
            stage3 = {"model": chairman, "response": "", "cost_usd": result.cost_usd, "error": error}
        await self.emit(ev.stage3_complete(stage3))
        return stage3

    # ── title ────────────────────────────────────────────────────────────────

    async def _title(self, result: TurnResult) -> None:
        """Best effort: the turn is already stored. Updates ``result`` and the stored turn in one write."""
        model = choose_title_model(self.deps.title_model, [m.id for m in self.members], self.chairman.id,
                                   self.deps.catalog)
        call = await self._call("title", model, title_messages(self.query))
        if not call.ok:
            self._record_failure("title", call)
        result.title = title_from_result(call)
        result.failures, result.usage, result.status = list(self.failures), self.usage.as_dict(), self._status()
        patch = {"failures": result.failures, "usage": result.usage, "status": result.status}
        try:
            self.deps.store.update_turn(self.conversation_id, self.run_id, patch, title=result.title)
        except Exception:  # noqa: BLE001 - the turn itself is safe; only the title is lost
            log.exception("could not store the title of conversation %s", self.conversation_id)
            return
        await self.emit(ev.title_complete(result.title))


def _stage1_entry(result: MemberResult) -> Dict[str, Any]:
    return {"model": result.model, "response": result.content, "latency_ms": result.latency_ms,
            "cost_usd": result.cost_usd}


def _stage2_entry(result: MemberResult) -> Dict[str, Any]:
    return {"model": result.model, "ranking": result.content, "parsed_ranking": parse_ranking_from_text(result.content)}

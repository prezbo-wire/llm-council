"""Task tracking for council runs (Mese ARCHITECTURE §I.4 step 9).

Every task a run starts is tracked so a client disconnect can cancel all of them; cancelling a task
that is inside ``MeseClient.stream_chat`` closes its httpx stream, which Mese sees as a disconnect.
Python 3.10 compatible (no ``TaskGroup``).
"""
from __future__ import annotations

import asyncio
from typing import Any, Coroutine, List, Optional, Set, TypeVar

T = TypeVar("T")


class TaskTracker:
    """Owns a set of tasks and can cancel and await all pending ones."""

    def __init__(self) -> None:
        self._tasks: Set["asyncio.Task[Any]"] = set()

    def spawn(self, coro: Coroutine[Any, Any, T], *, name: Optional[str] = None) -> "asyncio.Task[T]":
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @property
    def pending(self) -> List["asyncio.Task[Any]"]:
        return [t for t in self._tasks if not t.done()]

    async def cancel_all(self) -> None:
        """Cancels every pending task and waits for all of them to finish (``return_exceptions=True``).

        The wait is shielded: if the caller is itself cancelled while waiting, the tasks still finish
        their cleanup (closing their Mese streams) on their own.
        """
        current = asyncio.current_task()
        while True:
            pending = [t for t in self.pending if t is not current]
            if not pending:
                return
            for task in pending:
                task.cancel()
            await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))

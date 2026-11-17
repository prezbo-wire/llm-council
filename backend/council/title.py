"""Conversation titles (Mese ARCHITECTURE §I.4 step 7).

The title runs after Stage 3, once per conversation: on the first turn, or on the next one when the first
turn was cancelled before its title call (``title_attempted`` is set by the first finished title call, even
a failed one, which is not retried on later turns). The model is ``TITLE_MODEL`` when it is set
**and** it is local or the council already contains a cloud model; otherwise the chairman. An
all-local council therefore never sends the question to a cloud model just for a title.

Billing (ChatGPT plan, Mese ``mese.billing``): an API-key-billed ``TITLE_MODEL`` also needs an API-key-billed
cloud model in the council. A council whose only cloud models are plan-billed (``chatgpt/*``) is titled by the
chairman, so choosing the ChatGPT plan never adds an API-key charge for the title.
"""
from __future__ import annotations

from typing import Iterable, Optional

from ..catalog import CatalogIndex
from ..contracts import MemberResult
from ..storage import DEFAULT_TITLE
from .prompts import build_title_prompt

MAX_TITLE_LENGTH = 50


def choose_title_model(title_model: Optional[str], members: Iterable[str], chairman: str,
                       catalog: CatalogIndex) -> str:
    """``TITLE_MODEL`` if allowed by the privacy and billing rules above, else the chairman."""
    if not title_model:
        return chairman
    title = catalog.info(title_model)
    if title.is_local:
        return title_model
    council = [catalog.info(model_id) for model_id in [*members, chairman]]   # unknown ids count as API-key cloud
    if any(not m.is_local and (title.is_plan or not m.is_plan) for m in council):
        return title_model
    return chairman


def title_messages(user_query: str) -> list:
    return [{"role": "user", "content": build_title_prompt(user_query)}]


def clean_title(text: str) -> str:
    """First non-empty line, without surrounding quotes, at most 50 characters."""
    line = next((ln.strip() for ln in text.strip().splitlines() if ln.strip()), "")
    title = line.strip('"\'').strip()
    if len(title) > MAX_TITLE_LENGTH:
        title = title[:MAX_TITLE_LENGTH - 3] + "..."
    return title or DEFAULT_TITLE


def title_from_result(result: MemberResult) -> str:
    """The cleaned title, or "New Conversation" when the call failed."""
    return clean_title(result.content) if result.ok else DEFAULT_TITLE

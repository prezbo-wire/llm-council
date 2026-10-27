"""Conversation titles (Mese ARCHITECTURE §I.4 step 7).

The title runs after Stage 3, once per conversation: on the first turn, or on the next one when the first
turn was cancelled before its title call (``title_attempted`` is set by the first finished title call, even
a failed one, which is not retried on later turns). The model is ``TITLE_MODEL`` when it is set
**and** it is local or the council already contains a cloud model; otherwise the chairman. An
all-local council therefore never sends the question to a cloud model just for a title.
"""
from __future__ import annotations

from typing import Iterable, Optional

from ..catalog import CatalogIndex, council_has_cloud
from ..contracts import MemberResult
from ..storage import DEFAULT_TITLE
from .prompts import build_title_prompt

MAX_TITLE_LENGTH = 50


def choose_title_model(title_model: Optional[str], members: Iterable[str], chairman: str,
                       catalog: CatalogIndex) -> str:
    """``TITLE_MODEL`` if allowed by the privacy rule above, else the chairman."""
    members = list(members)
    if title_model and (catalog.info(title_model).is_local or council_has_cloud(members, chairman, catalog)):
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

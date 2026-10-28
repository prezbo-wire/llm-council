"""Request models for the llm-council REST API (Mese ARCHITECTURE §I.5). Owned by WP0; read-only.

Bodies are snake_case. Only syntax is checked here; semantic checks against the Mese catalog
(unknown_models, chairman_cannot_chair, mese_unreachable) happen in backend/catalog.py (WP7).
Compatible with Python 3.10.
"""
from __future__ import annotations

import re
from typing import Annotated, List, Optional, TypedDict

from pydantic import BaseModel, Field, field_validator

MAX_MEMBERS = 8
#: Council member / chairman ids: '<provider|node>/<model>' (also matches 'ft/<slug>-v<N>').
MODEL_ID_PATTERN = r"^[a-z0-9][a-z0-9-]*/\S{1,200}$"
#: User preset ids (PUT/DELETE /api/presets/{id}).
PRESET_ID_PATTERN = r"^[a-z0-9-]{1,40}$"
#: Built-in preset ids are selectors resolved at request time and are immutable (409 on PUT/DELETE).
BUILTIN_PRESET_IDS = ("builtin:all-local", "builtin:frontier-cloud", "builtin:hybrid")
#: A preset reference: a built-in id or a user preset id.
PRESET_REF_PATTERN = r"^(builtin:[a-z0-9-]{1,40}|[a-z0-9-]{1,40})$"

_MODEL_ID_RE = re.compile(MODEL_ID_PATTERN)
_PRESET_ID_RE = re.compile(PRESET_ID_PATTERN)

ModelId = Annotated[str, Field(pattern=MODEL_ID_PATTERN, max_length=233)]
PresetRef = Annotated[str, Field(pattern=PRESET_REF_PATTERN)]


def is_model_id(value: str) -> bool:
    return bool(_MODEL_ID_RE.match(value))


def is_user_preset_id(value: str) -> bool:
    """True for ids usable with PUT/DELETE /api/presets/{id}; 'builtin:*' is never a user id."""
    return bool(_PRESET_ID_RE.match(value))


def is_builtin_preset_id(value: str) -> bool:
    return value.startswith("builtin:")


class CouncilConfig(BaseModel):
    """1–8 unique members plus a required chairman (the chairman may or may not be a member)."""

    members: List[ModelId] = Field(min_length=1, max_length=MAX_MEMBERS)
    chairman: ModelId

    @field_validator("members")
    @classmethod
    def _unique_members(cls, members: List[str]) -> List[str]:
        seen = set()
        dupes = []
        for m in members:
            if m in seen and m not in dupes:
                dupes.append(m)
            seen.add(m)
        if dupes:
            raise ValueError(f"duplicate council members: {', '.join(dupes)}")
        return members

    def as_dict(self) -> dict:
        """CouncilConfigDict (backend/contracts.py)."""
        return {"members": list(self.members), "chairman": self.chairman}


class SendMessageRequest(BaseModel):
    """POST /api/conversations/{id}/message and /message/stream/v2."""

    content: str = Field(min_length=1)
    council: Optional[CouncilConfig] = None


class CreateConversationRequest(BaseModel):
    """POST /api/conversations. A council wins over preset_id when both are given."""

    council: Optional[CouncilConfig] = None
    preset_id: Optional[PresetRef] = None


class PresetPutRequest(CouncilConfig):
    """PUT /api/presets/{id} (the id is a path parameter matching PRESET_ID_PATTERN)."""

    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("name must not be blank")
        return name


class ConversationCouncilPut(CouncilConfig):
    """PUT /api/conversations/{id}/council: CouncilConfig & {"preset_id"?: str}. Applies to the next message."""

    preset_id: Optional[PresetRef] = None


# ── Response shapes (§I.5), for typing only ──────────────────────────────────


class CouncilModelProvider(TypedDict):
    id: str
    name: str


class CouncilModelNode(TypedDict):
    id: str
    name: str
    runtime: str
    host_id: str


class PricePerMtok(TypedDict):
    input: float
    output: float


class LineageRef(TypedDict):
    name: str
    version: str


class CouncilModel(TypedDict):
    """V1Model projected for the picker (GET /api/models)."""

    id: str
    name: str
    kind: str  # 'cloud' | 'local' | 'custom'
    provider: Optional[CouncilModelProvider]
    node: Optional[CouncilModelNode]
    available: bool
    unavailable_reason: Optional[str]
    load_state: Optional[str]
    can_rank: bool
    can_chair: bool
    context_length: Optional[int]
    size_bytes: Optional[int]
    price_per_mtok: Optional[PricePerMtok]  # USD per 1M; None for local/unknown
    price_verified: Optional[bool]
    tags: List[str]
    lineage: Optional[LineageRef]


class Preset(TypedDict):
    id: str  # 'builtin:all-local' | 'builtin:frontier-cloud' | 'builtin:hybrid' | user slug
    name: str
    description: Optional[str]
    members: List[str]
    chairman: Optional[str]  # None when a built-in can't resolve
    builtin: bool
    unavailable: List[str]
    created_at: Optional[str]
    updated_at: Optional[str]


class ConversationCouncil(TypedDict):
    members: List[str]
    chairman: str
    preset_id: Optional[str]
    updated_at: str

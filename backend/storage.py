"""JSON file storage for conversations (Mese ARCHITECTURE §I.7).

Every write is atomic: the JSON goes to a temporary file in the same directory, is fsynced, and then
replaces the target with ``os.replace``. A crash at any point leaves either the old or the new file.

Old conversation files load unchanged: every read uses ``.get()`` with a default, and all new fields
(``council`` on the conversation; ``metadata``, ``council_snapshot``, ``failures``, ``usage`` and
``status`` on assistant messages) are optional.

An assistant message's ``status`` is ``complete`` or ``partial`` for a finished turn, and ``cancelled``
(client gone) or ``error`` (unexpected exception) for a turn that stopped early; the runner always
persists one of these, so a user message is never left without its assistant message.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DEFAULT_TITLE = "New Conversation"
#: Conversation ids are UUIDs today; anything outside this set never reaches the filesystem.
CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def utc_now_iso() -> str:
    """``2026-10-27T12:00:00Z`` (used for council and preset timestamps)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _created_at_now() -> str:
    # Same naive-UTC format the conversation files have always used, so list sorting stays stable.
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def atomic_write_json(path: os.PathLike, data: Any) -> None:
    """Writes ``data`` as JSON to ``path`` atomically (temp file in the same dir, fsync, ``os.replace``)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2, ensure_ascii=False)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    _fsync_dir(target.parent)


def _fsync_dir(directory: Path) -> None:
    """Persists the rename itself (best effort; not every platform allows opening a directory)."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def is_valid_conversation_id(conversation_id: str) -> bool:
    return bool(CONVERSATION_ID_RE.match(conversation_id))


def council_record(members: List[str], chairman: str, preset_id: Optional[str] = None) -> Dict[str, Any]:
    """A ``ConversationCouncil``: ``{members, chairman, preset_id, updated_at}``."""
    return {"members": list(members), "chairman": chairman, "preset_id": preset_id, "updated_at": utc_now_iso()}


def normalize_conversation(data: Dict[str, Any]) -> Dict[str, Any]:
    """Fills defaults for legacy files without touching any stored field."""
    conversation = dict(data)
    conversation.setdefault("created_at", "")
    conversation["title"] = data.get("title") or DEFAULT_TITLE
    conversation["council"] = data.get("council")
    messages = data.get("messages")
    conversation["messages"] = messages if isinstance(messages, list) else []
    return conversation


class ConversationNotFound(KeyError):
    """The conversation id is unknown (or not a valid id)."""


class ConversationStore:
    """Conversations stored as ``<directory>/<id>.json``."""

    def __init__(self, directory: os.PathLike) -> None:
        self.directory = Path(directory)

    def _path(self, conversation_id: str) -> Path:
        if not is_valid_conversation_id(conversation_id):
            raise ConversationNotFound(conversation_id)
        return self.directory / f"{conversation_id}.json"

    def _read(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            log.error("cannot read conversation file %s: %s", path.name, exc)
            return None
        return data if isinstance(data, dict) else None

    # ── reads ────────────────────────────────────────────────────────────────

    def get(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """The conversation with defaults filled in, or None."""
        try:
            path = self._path(conversation_id)
        except ConversationNotFound:
            return None
        data = self._read(path)
        if data is None:
            return None
        data.setdefault("id", conversation_id)
        return normalize_conversation(data)

    def list(self) -> List[Dict[str, Any]]:
        """Metadata for every conversation, newest first."""
        if not self.directory.is_dir():
            return []
        items = []
        for path in self.directory.glob("*.json"):
            if path.name.startswith("."):
                continue
            data = self._read(path)
            if data is None:
                continue
            messages = data.get("messages") if isinstance(data.get("messages"), list) else []
            items.append({"id": data.get("id") or path.stem, "created_at": data.get("created_at") or "",
                          "title": data.get("title") or DEFAULT_TITLE, "message_count": len(messages)})
        items.sort(key=lambda item: item["created_at"], reverse=True)
        return items

    # ── writes ───────────────────────────────────────────────────────────────

    def save(self, conversation: Dict[str, Any]) -> None:
        atomic_write_json(self._path(conversation["id"]), conversation)

    def create(self, conversation_id: str, council: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        conversation = {"id": conversation_id, "created_at": _created_at_now(), "title": DEFAULT_TITLE,
                        "council": council, "messages": []}
        self.save(conversation)
        return normalize_conversation(conversation)

    def _require(self, conversation_id: str) -> Dict[str, Any]:
        conversation = self.get(conversation_id)
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        return conversation

    def add_user_message(self, conversation_id: str, content: str) -> None:
        conversation = self._require(conversation_id)
        conversation["messages"].append({"role": "user", "content": content})
        self.save(conversation)

    def add_assistant_message(self, conversation_id: str, message: Dict[str, Any]) -> None:
        """Appends an assistant message (``role`` is forced to ``assistant``)."""
        conversation = self._require(conversation_id)
        conversation["messages"].append({**message, "role": "assistant"})
        self.save(conversation)

    def update_title(self, conversation_id: str, title: str) -> None:
        conversation = self._require(conversation_id)
        conversation["title"] = title
        self.save(conversation)

    def update_turn(self, conversation_id: str, run_id: str, patch: Dict[str, Any], *,
                    title: Optional[str] = None) -> bool:
        """Merges ``patch`` into the assistant message of run ``run_id`` and, when given, sets ``title``.

        A given ``title`` also marks the conversation ``title_attempted`` (a finished title call, even one
        that failed and left the default title), so later turns do not ask for a title again. Both changes
        land in one atomic write. Returns False (and still saves the title) when no assistant message
        carries that ``council_snapshot.run_id``.
        """
        conversation = self._require(conversation_id)
        found = False
        for message in reversed(conversation["messages"]):
            snapshot = message.get("council_snapshot") if isinstance(message, dict) else None
            if message.get("role") == "assistant" and isinstance(snapshot, dict) and snapshot.get("run_id") == run_id:
                message.update({key: value for key, value in patch.items() if key != "role"})
                found = True
                break
        if title is not None:
            conversation["title"] = title
            conversation["title_attempted"] = True
        if found or title is not None:
            self.save(conversation)
        return found

    def set_council(self, conversation_id: str, members: List[str], chairman: str,
                    preset_id: Optional[str] = None) -> Dict[str, Any]:
        """Stores the council used for the *next* message; returns the ``ConversationCouncil``."""
        conversation = self._require(conversation_id)
        council = council_record(members, chairman, preset_id)
        conversation["council"] = council
        self.save(conversation)
        return council

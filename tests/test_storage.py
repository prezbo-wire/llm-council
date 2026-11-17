"""Atomic conversation/preset storage and legacy compatibility (§I.7)."""
from __future__ import annotations

import json
import os
import shutil

import pytest

from backend import storage
from backend.presets import PresetNotFound, PresetStore
from backend.storage import DEFAULT_TITLE, ConversationNotFound, ConversationStore, atomic_write_json
from conftest import CONVERSATION_FIXTURES


def _leftover_temp_files(directory):
    return [p.name for p in directory.iterdir() if p.name.startswith(".")]


def test_crash_between_write_and_replace_keeps_the_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "conv.json"
    atomic_write_json(target, {"version": "old"})
    before = target.read_bytes()

    def crash(*_args, **_kwargs):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(storage.os, "replace", crash)
    with pytest.raises(OSError, match="simulated crash"):
        atomic_write_json(target, {"version": "new", "payload": "x" * 10_000})
    assert target.read_bytes() == before
    assert _leftover_temp_files(tmp_path) == []


def test_hard_interrupt_during_write_keeps_the_previous_file(tmp_path, monkeypatch):
    target = tmp_path / "conv.json"
    atomic_write_json(target, {"version": "old"})
    before = target.read_bytes()

    def interrupted(_fd):
        raise KeyboardInterrupt  # e.g. the process is killed while syncing the temp file

    monkeypatch.setattr(storage.os, "fsync", interrupted)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_json(target, {"version": "new"})
    assert target.read_bytes() == before and _leftover_temp_files(tmp_path) == []


def test_atomic_write_writes_through_a_temp_file_in_the_same_directory(tmp_path, monkeypatch):
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((os.path.dirname(src), os.path.dirname(str(dst))))
        return real_replace(src, dst)

    monkeypatch.setattr(storage.os, "replace", spy)
    atomic_write_json(tmp_path / "sub" / "x.json", {"a": 1})
    assert seen == [(str(tmp_path / "sub"), str(tmp_path / "sub"))]
    assert json.loads((tmp_path / "sub" / "x.json").read_text()) == {"a": 1}


def test_store_crash_during_save_keeps_the_conversation(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    store.create("conv-1")
    store.add_user_message("conv-1", "first")
    monkeypatch.setattr(storage.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        store.add_user_message("conv-1", "second")
    monkeypatch.undo()
    assert [m["content"] for m in store.get("conv-1")["messages"]] == ["first"]


@pytest.fixture
def legacy_store(tmp_path):
    for path in CONVERSATION_FIXTURES.glob("legacy_*.json"):
        conversation_id = json.loads(path.read_text())["id"]
        shutil.copyfile(path, tmp_path / f"{conversation_id}.json")
    return ConversationStore(tmp_path)


def test_legacy_conversations_load_with_their_shapes_unchanged(legacy_store, legacy_conversations):
    for name, original in legacy_conversations.items():
        loaded = legacy_store.get(original["id"])
        assert loaded is not None, name
        assert loaded["messages"] == original["messages"], name
        assert (loaded["title"], loaded["created_at"], loaded["council"]) == (original["title"], original["created_at"], None)
        for message in loaded["messages"]:
            if message["role"] == "assistant":
                assert set(message) == {"role", "stage1", "stage2", "stage3"}
    listed = legacy_store.list()
    assert [c["created_at"] for c in listed] == sorted((c["created_at"] for c in listed), reverse=True)
    assert {c["id"]: c["message_count"] for c in listed}["0dec6160-0000-4000-8000-000000000004"] == 4


def test_appending_to_a_legacy_conversation_keeps_old_messages_intact(legacy_store, legacy_conversations):
    original = legacy_conversations["legacy_openrouter_v2_latency.json"]
    legacy_store.add_user_message(original["id"], "Follow-up")
    legacy_store.add_assistant_message(original["id"], {"stage1": [], "stage2": [], "stage3": {"model": "a/b", "response": "", "error": None}})
    loaded = legacy_store.get(original["id"])
    assert loaded["messages"][: len(original["messages"])] == original["messages"]
    assert loaded["messages"][-1]["role"] == "assistant"


def test_set_council_and_new_conversation_fields(tmp_path):
    store = ConversationStore(tmp_path)
    created = store.create("conv-2")
    assert (created["council"], created["title"], created["messages"]) == (None, "New Conversation", [])
    council = store.set_council("conv-2", ["a/x", "b/y"], "a/x", "builtin:hybrid")
    assert council["members"] == ["a/x", "b/y"] and council["preset_id"] == "builtin:hybrid"
    assert council["updated_at"].endswith("Z")
    assert store.get("conv-2")["council"] == council
    store.update_title("conv-2", "Hello")
    assert store.list() == [{"id": "conv-2", "created_at": created["created_at"], "title": "Hello", "message_count": 0}]


def test_invalid_ids_never_touch_the_filesystem(tmp_path):
    store = ConversationStore(tmp_path / "conversations")
    for bad in ("..", "../x", "a/b", ".hidden", "", "x" * 200):
        assert store.get(bad) is None
        with pytest.raises(ConversationNotFound):
            store.add_user_message(bad, "x")
    with pytest.raises(ConversationNotFound):
        store.add_user_message("valid-but-missing", "x")


def test_list_skips_corrupt_and_temp_files(tmp_path):
    store = ConversationStore(tmp_path)
    store.create("good")
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / ".good.json.abc.tmp").write_text("{}")
    assert [c["id"] for c in store.list()] == ["good"]


def test_presets_file_roundtrip_and_quarantine(tmp_path):
    path = tmp_path / "presets.json"
    presets = PresetStore(path)
    assert presets.list() == []
    first = presets.put("team", "Team", ["a/x"], "a/x")
    second = presets.put("team", "Team 2", ["a/x", "b/y"], "b/y")
    assert second["created_at"] == first["created_at"]
    data = json.loads(path.read_text())
    assert data == {"version": 1, "presets": [second]}
    with pytest.raises(PresetNotFound):
        presets.delete("other")
    with pytest.raises(ValueError):
        presets.put("builtin:x", "X", ["a/x"], "a/x")
    path.write_text("{corrupt")
    assert presets.list() == []
    assert not path.exists() and any(p.name.startswith("presets.json.corrupt-") for p in tmp_path.iterdir())
    data_with_junk = {"version": 1, "presets": [second, {"id": "BAD ID", "name": 1}]}
    path.write_text(json.dumps(data_with_junk))
    assert [p["id"] for p in presets.list()] == ["team"]


def test_update_turn_patches_the_run_and_sets_the_title_in_one_write(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    store.create("conv-3")
    store.add_user_message("conv-3", "q1")
    store.add_assistant_message("conv-3", {"stage1": [], "council_snapshot": {"run_id": "run_a"}, "status": "complete"})
    store.add_user_message("conv-3", "q2")
    store.add_assistant_message("conv-3", {"stage1": [], "council_snapshot": {"run_id": "run_b"}, "status": "complete"})
    writes = []
    real_save = store.save
    monkeypatch.setattr(store, "save", lambda conversation: (writes.append(1), real_save(conversation)))

    assert store.update_turn("conv-3", "run_a", {"status": "partial", "role": "user", "failures": [{"stage": "title"}]},
                             title="Hello") is True
    assert len(writes) == 1
    saved = store.get("conv-3")
    assert saved["title"] == "Hello"
    assert saved["messages"][1] == {"role": "assistant", "stage1": [], "council_snapshot": {"run_id": "run_a"},
                                    "status": "partial", "failures": [{"stage": "title"}]}
    assert saved["messages"][3]["status"] == "complete"  # another run is untouched

    assert store.update_turn("conv-3", "run_missing", {"status": "x"}, title="Still saved") is False
    assert store.get("conv-3")["title"] == "Still saved"
    with pytest.raises(ConversationNotFound):
        store.update_turn("nope", "run_a", {})


def test_a_finished_title_call_marks_the_conversation_title_attempted(tmp_path):
    """RV-CD-1: even a failed title (the default title is stored) marks the conversation; a patch alone does not."""
    store = ConversationStore(tmp_path)
    store.create("conv-4")
    store.add_user_message("conv-4", "q1")
    store.add_assistant_message("conv-4", {"stage1": [], "council_snapshot": {"run_id": "run_a"}, "status": "complete"})
    assert "title_attempted" not in store.get("conv-4")
    store.update_turn("conv-4", "run_a", {"status": "complete"})
    assert "title_attempted" not in store.get("conv-4")
    store.update_turn("conv-4", "run_a", {"status": "partial"}, title=DEFAULT_TITLE)
    saved = store.get("conv-4")
    assert saved["title"] == DEFAULT_TITLE and saved["title_attempted"] is True

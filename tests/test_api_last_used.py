"""The "Last used" council (``builtin:last-used``) through the ASGI app with a mock Mese.

It lives apart from ``test_api.py`` only to keep that module under 500 lines; it reuses its fixtures.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from backend.main import create_app
from test_api import BASE_URL, COUNCIL, _new_conversation, api, mock, settings  # noqa: F401 - fixtures
from test_support import FT, GPT, OPUS, QWEN, make_client

PAIR = {"members": [OPUS, GPT], "chairman": OPUS}


async def _last_used(client):
    presets = (await client.get("/api/presets")).json()["presets"]
    assert presets[0]["id"] == "builtin:last-used"
    return presets[0]


async def _send(client, conv, path="/message", **body):
    response = await client.post(f"/api/conversations/{conv['id']}{path}", json={"content": "Hi?", **body})
    assert response.status_code == 200, response.text
    return response


async def test_presets_list_last_used_first_and_empty_before_any_send(api):
    presets = (await api.get("/api/presets")).json()["presets"]
    assert [p["id"] for p in presets] == ["builtin:last-used", "builtin:all-local", "builtin:frontier-cloud",
                                          "builtin:hybrid"]
    last = presets[0]
    assert (last["name"], last["builtin"], last["members"], last["chairman"], last["unavailable"]) == \
        ("Last used", True, [], None, [])
    assert (last["updated_at"], last["source_preset_id"], last["conversation_id"]) == (None, None, None)


@pytest.mark.parametrize("path", ["/message", "/message/stream/v2"])
async def test_a_send_records_the_last_used_council(api, settings, path):
    first, second = await _new_conversation(api), await _new_conversation(api)
    for conv, council in ((first, COUNCIL), (second, PAIR)):
        await _send(api, conv, path, council=council)
        last = await _last_used(api)
        assert (last["members"], last["chairman"], last["conversation_id"]) == (council["members"], OPUS, conv["id"])
        assert last["updated_at"].endswith("Z") and last["source_preset_id"] is None and last["unavailable"] == []
    assert Path(settings.data_dir, "last_council.json").is_file()


async def test_a_rejected_send_leaves_last_used_unchanged(api, mock):
    ctx = api.app.state.ctx
    ctx.last_used.record([QWEN], OPUS, None, "earlier")
    before = ctx.last_used.get()
    conv = await _new_conversation(api)
    url = f"/api/conversations/{conv['id']}/message/stream/v2"
    mock.unreachable = True  # before any catalog fetch, so the client has nothing cached
    assert (await api.post(url, json={"content": "x", "council": COUNCIL})).status_code == 503
    mock.unreachable = False
    unknown = await api.post(url, json={"content": "x", "council": {"members": [OPUS, "nope/model-x"], "chairman": OPUS}})
    non_chair = await api.post(url, json={"content": "x", "council": {"members": [OPUS, QWEN], "chairman": FT}})
    assert (unknown.status_code, non_chair.status_code) == (422, 422)
    assert ctx.last_used.get() == before
    assert (await _last_used(api))["conversation_id"] == "earlier"


async def test_last_used_carries_the_saved_preset_id(api):
    conv = await _new_conversation(api)
    put = await api.put(f"/api/conversations/{conv['id']}/council", json={**COUNCIL, "preset_id": "my-team"})
    assert put.status_code == 200
    await _send(api, conv)  # no council in the request: the saved one, with its preset label
    assert (await _last_used(api))["source_preset_id"] == "my-team"
    created = await _new_conversation(api, preset_id="builtin:last-used")
    council = created["council"]
    assert (council["members"], council["chairman"], council["preset_id"]) == (COUNCIL["members"], OPUS, "my-team")


async def test_create_conversation_from_last_used_before_any_send_has_no_council(api):
    assert (await _new_conversation(api, preset_id="builtin:last-used"))["council"] is None


async def test_put_council_never_stores_the_last_used_pointer(api):
    first = await _new_conversation(api)
    await api.put(f"/api/conversations/{first['id']}/council", json={**COUNCIL, "preset_id": "my-team"})
    await _send(api, first)
    second = await _new_conversation(api)
    url = f"/api/conversations/{second['id']}/council"
    reordered = {"members": list(reversed(COUNCIL["members"])), "chairman": OPUS, "preset_id": "builtin:last-used"}
    assert (await api.put(url, json=reordered)).json()["council"]["preset_id"] == "my-team"
    other = await api.put(url, json={**PAIR, "preset_id": "builtin:last-used"})
    assert other.status_code == 200 and other.json()["council"]["preset_id"] is None
    assert api.app.state.ctx.store.get(second["id"])["council"]["preset_id"] is None
    with_council = await _new_conversation(api, council=PAIR, preset_id="builtin:last-used")
    assert with_council["council"]["preset_id"] is None


async def test_last_used_survives_a_restart(mock, settings):
    async def with_app(step):
        mese = make_client(mock)
        app = create_app(settings, mese_client=mese)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client:
            result = await step(client)
        await mese.aclose()
        return result

    async def send(client):
        conv = await _new_conversation(client)
        await _send(client, conv, council=COUNCIL)
        return conv

    conv = await with_app(send)
    last = await with_app(_last_used)
    assert (last["members"], last["chairman"], last["conversation_id"]) == (COUNCIL["members"], OPUS, conv["id"])


async def test_last_used_drops_a_model_that_left_the_catalog(api, mock):
    await _send(api, await _new_conversation(api), council=COUNCIL)
    mock.models = {**mock.models, "data": [m for m in mock.models["data"] if m["id"] != GPT]}
    assert (await api.get("/api/models?refresh=1")).status_code == 200
    last = await _last_used(api)
    assert (last["members"], last["chairman"], last["unavailable"]) == ([OPUS, QWEN], OPUS, [GPT])
    assert (await _new_conversation(api, preset_id="builtin:last-used"))["council"]["members"] == [OPUS, QWEN]


async def test_a_failing_last_used_write_never_fails_the_send(api, monkeypatch, caplog):
    def broken(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(api.app.state.ctx.last_used, "record", broken)
    conv = await _new_conversation(api)
    for path in ("/message", "/message/stream/v2"):
        await _send(api, conv, path, council=COUNCIL)
    assert "could not record the last-used council" in caplog.text
    assert (await _last_used(api))["members"] == []

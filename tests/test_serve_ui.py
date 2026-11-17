"""deploy/spark/serve_ui.py: cache headers, no directory listings, nothing outside the dist directory."""
from __future__ import annotations

import importlib.util
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SERVE_UI = Path(__file__).resolve().parents[1] / "deploy" / "spark" / "serve_ui.py"


def _load_serve_ui():
    spec = importlib.util.spec_from_file_location("serve_ui", SERVE_UI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


serve_ui = _load_serve_ui()
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(url: str):
    """(status, headers) of a GET; HTTP errors are returned, not raised."""
    try:
        with _OPENER.open(url, timeout=5) as response:
            return response.status, response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.headers


@pytest.fixture
def ui_url(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>llm-council</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside the dist directory")
    server = serve_ui.make_server(str(dist), "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_index_is_revalidated_on_every_load(ui_url: str) -> None:
    status, headers = _get(f"{ui_url}/")
    assert status == 200
    assert headers["Cache-Control"] == "no-cache"
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_index_with_a_query_string_is_not_cached_either(ui_url: str) -> None:
    status, headers = _get(f"{ui_url}/?replay=hybrid")
    assert status == 200
    assert headers["Cache-Control"] == "no-cache"


def test_hashed_assets_are_cached_for_a_year(ui_url: str) -> None:
    status, headers = _get(f"{ui_url}/assets/index-abc123.js")
    assert status == 200
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_a_missing_asset_is_not_cached(ui_url: str) -> None:
    status, headers = _get(f"{ui_url}/assets/index-gone.js")
    assert status == 404
    assert headers["Cache-Control"] == "no-cache"


def test_directory_listings_are_off(ui_url: str) -> None:
    status, headers = _get(f"{ui_url}/assets/")
    assert status == 404
    assert headers["Cache-Control"] == "no-cache"


def test_paths_outside_the_dist_directory_are_not_served(ui_url: str) -> None:
    status, _ = _get(f"{ui_url}/../secret.txt")
    assert status == 404
    status, _ = _get(f"{ui_url}/%2e%2e/secret.txt")
    assert status == 404


def test_main_refuses_a_directory_without_index_html(tmp_path: Path) -> None:
    assert serve_ui.main([str(tmp_path)]) == 1


def test_main_rejects_a_non_numeric_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "index.html").write_text("<!doctype html>")
    monkeypatch.setenv("COUNCIL_UI_PORT", "http")
    assert serve_ui.main([str(tmp_path)]) == 2

"""Settings parsing (§I.8): env vars, safe logging helpers."""
from __future__ import annotations

import pytest

from backend.config import DEFAULT_ALLOWED_ORIGINS, host_of, load_settings, mask_secret, safe_url


def test_defaults():
    settings = load_settings({})
    assert settings.mese_url == "http://127.0.0.1:8790" and settings.mese_api_key is None
    assert settings.allowed_origins == DEFAULT_ALLOWED_ORIGINS
    assert (settings.council_host, settings.council_port) == ("0.0.0.0", 8001)
    assert settings.conversations_dir == "data/conversations" and settings.presets_path == "data/presets.json"
    assert settings.title_model is None and settings.default_council == ()


def test_env_values():
    settings = load_settings({
        "MESE_URL": "http://10.0.0.2:8790/", "MESE_API_KEY": " mese_ck_abcdefghijklmnop ", "TITLE_MODEL": "spark/x",
        "DEFAULT_COUNCIL": "a/x, b/y,,a/x", "DEFAULT_CHAIRMAN": "a/x", "COUNCIL_DATA_DIR": "/tmp/council",
        "COUNCIL_ALLOWED_ORIGINS": "http://192.168.1.5:5173, http://localhost:5173", "COUNCIL_PORT": "9001",
    })
    assert settings.mese_url == "http://10.0.0.2:8790" and settings.mese_api_key == "mese_ck_abcdefghijklmnop"
    assert settings.default_council == ("a/x", "b/y") and settings.default_chairman == "a/x"
    assert settings.presets_path == "/tmp/council/presets.json" and settings.council_port == 9001
    assert settings.allowed_origins == ("http://192.168.1.5:5173", "http://localhost:5173")
    assert "mese_ck_abcdefghijklmnop" not in repr(settings) and "mese_ck_abcdefghijklmnop" not in settings.describe()
    assert "…mnop" in settings.describe()


def test_default_council_needs_a_chairman():
    assert load_settings({"DEFAULT_COUNCIL": "a/x"}).default_council == ()


def test_bad_port():
    with pytest.raises(ValueError):
        load_settings({"COUNCIL_PORT": "http"})


def test_secret_helpers():
    assert mask_secret(None) == "(not set)" and mask_secret("short") == "…(set)"
    assert mask_secret("mese_ck_0123456789") == "…6789"
    assert safe_url("http://user:pw@127.0.0.1:8790/x") == "http://127.0.0.1:8790/x"
    assert safe_url("http://127.0.0.1:8790") == "http://127.0.0.1:8790"


def test_allowed_hosts_follow_the_cors_origins():
    assert load_settings({}).allowed_hosts == frozenset({"localhost", "127.0.0.1", "::1"})
    settings = load_settings({
        "COUNCIL_ALLOWED_ORIGINS": "http://localhost:5173,http://192.168.1.5:5173,http://[fd7a:115c::5]:5173,"
                                   "https://Mac.Tail1234.ts.net",
        "COUNCIL_HOST": "100.64.0.9",
    })
    assert settings.allowed_hosts == frozenset({"localhost", "127.0.0.1", "::1", "192.168.1.5", "fd7a:115c::5",
                                                "mac.tail1234.ts.net", "100.64.0.9"})
    assert "hosts " in settings.describe() and "192.168.1.5" in settings.describe()
    for wildcard in ("0.0.0.0", "::", "[::]"):
        assert load_settings({"COUNCIL_HOST": wildcard}).allowed_hosts == frozenset({"localhost", "127.0.0.1", "::1"})
    assert load_settings({"COUNCIL_ALLOWED_ORIGINS": "*"}).allowed_hosts is None


def test_host_of_parses_host_headers():
    assert [host_of(v) for v in ("LocalHost:8001", "[::1]:8001", "[::1]", "a.b", "::1", "", None, "[bad")] == \
        ["localhost", "::1", "::1", "a.b", "::1", "", "", ""]

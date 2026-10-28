"""Configuration for llm-council (Mese ARCHITECTURE §I.8).

llm-council is a plain client of Mese, the local model router. Every model call goes to
``MESE_URL`` with ``MESE_API_KEY``. Provider keys, nodes, loading and usage live in Mese.

Environment variables (``.env`` is loaded first; real environment variables win):

=========================  ===============================================================
``MESE_URL``               Mese inference API (default ``http://127.0.0.1:8790``)
``MESE_API_KEY``           Mese client token (``mese_ck_…``). Keep it in ``.env``; it is
                           never logged except as ``…last4``.
``MESE_ADMIN_URL``         Mese admin panel, linked from the UI (default
                           ``http://127.0.0.1:8791/admin/``)
``TITLE_MODEL``            Optional model id for conversation titles. It is only used when it
                           is local or the council already contains a cloud model.
``DEFAULT_COUNCIL``        Optional comma list of member ids used when a conversation has no
                           council; needs ``DEFAULT_CHAIRMAN`` too.
``DEFAULT_CHAIRMAN``       Optional chairman id for ``DEFAULT_COUNCIL``.
``COUNCIL_DATA_DIR``       Data directory (default ``data``): conversations live in
                           ``<dir>/conversations``.
``PRESETS_PATH``           User presets file (default ``<COUNCIL_DATA_DIR>/presets.json``).
``COUNCIL_HOST``           Bind address (default ``0.0.0.0``).
``COUNCIL_PORT``           Bind port (default ``8001``).
``COUNCIL_ALLOWED_ORIGINS``  Comma list of CORS origins (default localhost and 127.0.0.1 on
                           ports 5173 and 3000). The start scripts add LAN and tailnet origins.
=========================  ===============================================================

The ``Host`` header is checked against the same policy (:attr:`Settings.allowed_hosts`): the hostnames
of ``COUNCIL_ALLOWED_ORIGINS``, ``localhost``/``127.0.0.1``/``::1``, and ``COUNCIL_HOST`` when it is a
specific address. Other hosts get 400, so a DNS-rebinding page cannot reach the API same-origin.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import FrozenSet, Mapping, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

DEFAULT_MESE_URL = "http://127.0.0.1:8790"
DEFAULT_MESE_ADMIN_URL = "http://127.0.0.1:8791/admin/"
DEFAULT_DATA_DIR = "data"
DEFAULT_COUNCIL_HOST = "0.0.0.0"
DEFAULT_COUNCIL_PORT = 8001
DEFAULT_ALLOWED_ORIGINS: Tuple[str, ...] = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

#: Hostnames always accepted in the ``Host`` header (a DNS-rebinding page cannot use them).
LOOPBACK_HOSTS: Tuple[str, ...] = ("localhost", "127.0.0.1", "::1")
#: ``COUNCIL_HOST`` values that bind every interface and therefore name no host.
WILDCARD_BIND_HOSTS = frozenset({"", "0.0.0.0", "::", "*"})

#: Read-only display names for the model ids stored in conversations from before Mese.
#: They are only used to render old conversations; old ids are never remapped to other models.
LEGACY_DISPLAY_NAMES: Mapping[str, str] = MappingProxyType({
    "openai/gpt-5.1": "GPT-5.1",
    "openai/gpt-oss-20b": "GPT-OSS 20B",
    "google/gemini-3-pro-preview": "Gemini 3 Pro",
    "google/gemini-2.5-flash": "Gemini 2.5 Flash",
    "anthropic/claude-sonnet-4.5": "Claude Sonnet 4.5",
    "x-ai/grok-4": "Grok 4",
})


def _split_list(value: Optional[str]) -> Tuple[str, ...]:
    """Splits a comma list, dropping blanks and duplicates while keeping order."""
    if not value:
        return ()
    items = (part.strip() for part in value.split(","))
    return tuple(dict.fromkeys(item for item in items if item))


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _port(value: Optional[str]) -> int:
    if not value:
        return DEFAULT_COUNCIL_PORT
    try:
        port = int(value)
    except ValueError:
        raise ValueError(f"COUNCIL_PORT must be an integer, got {value!r}") from None
    if not 0 < port < 65536:
        raise ValueError(f"COUNCIL_PORT out of range: {port}")
    return port


def mask_secret(value: Optional[str]) -> str:
    """Renders a secret for logs: ``…last4`` at most, never the value itself."""
    if not value:
        return "(not set)"
    return "…" + value[-4:] if len(value) > 8 else "…(set)"


def safe_url(url: str) -> str:
    """Drops any ``user:password@`` part so a URL can be logged or returned to the UI."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.username is None and parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def host_of(value: Optional[str]) -> str:
    """Lower-case hostname of a ``Host`` header or bind address: ``[::1]:8001`` → ``::1``, ``a.b:80`` → ``a.b``."""
    text = (value or "").strip().lower()
    if text.startswith("["):
        end = text.find("]")
        return text[1:end] if end > 0 else ""
    if text.count(":") == 1:  # host:port (a bare IPv6 address has several colons)
        return text.split(":", 1)[0]
    return text


def origin_host(origin: str) -> Optional[str]:
    """Hostname of a CORS origin (``http://[::1]:5173`` → ``::1``), or None when it has none."""
    try:
        return urlsplit(origin.strip()).hostname or None
    except ValueError:
        return None


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration. Build it with :func:`load_settings` (or directly in tests)."""

    mese_url: str = DEFAULT_MESE_URL
    mese_api_key: Optional[str] = field(default=None, repr=False)
    mese_admin_url: str = DEFAULT_MESE_ADMIN_URL
    title_model: Optional[str] = None
    default_council: Tuple[str, ...] = ()
    default_chairman: Optional[str] = None
    data_dir: str = DEFAULT_DATA_DIR
    presets_path: str = os.path.join(DEFAULT_DATA_DIR, "presets.json")
    council_host: str = DEFAULT_COUNCIL_HOST
    council_port: int = DEFAULT_COUNCIL_PORT
    allowed_origins: Tuple[str, ...] = DEFAULT_ALLOWED_ORIGINS

    @property
    def conversations_dir(self) -> str:
        return os.path.join(self.data_dir, "conversations")

    @property
    def token_set(self) -> bool:
        return bool(self.mese_api_key)

    @property
    def allowed_hosts(self) -> Optional[FrozenSet[str]]:
        """``Host`` allowlist matching the CORS policy, or None (any host) when the origins include ``*``."""
        if "*" in self.allowed_origins:
            return None
        hosts = set(LOOPBACK_HOSTS)
        hosts.update(host for host in map(origin_host, self.allowed_origins) if host)
        bind = host_of(self.council_host)  # "[::]" → "::"
        if bind not in WILDCARD_BIND_HOSTS:
            hosts.add(bind)
        return frozenset(hosts)

    def describe(self) -> str:
        """One log line with no secret in it."""
        hosts = self.allowed_hosts
        return (f"Mese {safe_url(self.mese_url)} (token {mask_secret(self.mese_api_key)}), "
                f"data {self.data_dir}, CORS {', '.join(self.allowed_origins)}, "
                f"hosts {', '.join(sorted(hosts)) if hosts is not None else 'any'}")


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Reads :class:`Settings` from ``env`` (default: ``os.environ`` after ``.env`` was loaded)."""
    env = os.environ if env is None else env
    data_dir = _clean(env.get("COUNCIL_DATA_DIR")) or DEFAULT_DATA_DIR
    default_council = _split_list(env.get("DEFAULT_COUNCIL"))
    default_chairman = _clean(env.get("DEFAULT_CHAIRMAN"))
    if bool(default_council) != bool(default_chairman):
        log.warning("DEFAULT_COUNCIL and DEFAULT_CHAIRMAN must be set together; ignoring both")
        default_council, default_chairman = (), None
    return Settings(
        mese_url=(_clean(env.get("MESE_URL")) or DEFAULT_MESE_URL).rstrip("/"),
        mese_api_key=_clean(env.get("MESE_API_KEY")),
        mese_admin_url=_clean(env.get("MESE_ADMIN_URL")) or DEFAULT_MESE_ADMIN_URL,
        title_model=_clean(env.get("TITLE_MODEL")),
        default_council=default_council,
        default_chairman=default_chairman,
        data_dir=data_dir,
        presets_path=_clean(env.get("PRESETS_PATH")) or os.path.join(data_dir, "presets.json"),
        council_host=_clean(env.get("COUNCIL_HOST")) or DEFAULT_COUNCIL_HOST,
        council_port=_port(_clean(env.get("COUNCIL_PORT"))),
        allowed_origins=_split_list(env.get("COUNCIL_ALLOWED_ORIGINS")) or DEFAULT_ALLOWED_ORIGINS,
    )


SETTINGS = load_settings()

# Module-level aliases of the active settings, for scripts and quick inspection.
MESE_URL = SETTINGS.mese_url
MESE_ADMIN_URL = SETTINGS.mese_admin_url
TITLE_MODEL = SETTINGS.title_model
DEFAULT_COUNCIL = SETTINGS.default_council
DEFAULT_CHAIRMAN = SETTINGS.default_chairman
DATA_DIR = SETTINGS.conversations_dir
PRESETS_PATH = SETTINGS.presets_path
COUNCIL_HOST = SETTINGS.council_host
COUNCIL_PORT = SETTINGS.council_port
COUNCIL_ALLOWED_ORIGINS = SETTINGS.allowed_origins

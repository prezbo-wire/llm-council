"""Serves the built llm-council UI (``frontend/dist``) for ``llm-council-ui.service``.

Python's ``http.server`` with three changes for a long-running deploy:

- ``index.html`` (and everything outside ``/assets/``) is sent with ``Cache-Control: no-cache``, so a
  browser picks up a redeploy on the next load instead of asking for hashed files that are gone;
- Vite's hashed files under ``/assets/`` are cached for a year;
- directory listings are off.

Usage: ``python -I serve_ui.py <dist dir>``. The bind address and port come from ``COUNCIL_HOST`` and
``COUNCIL_UI_PORT`` (defaults ``127.0.0.1`` and ``5173``).
"""
from __future__ import annotations

import functools
import os
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5173


class UiHandler(SimpleHTTPRequestHandler):
    """Static files with cache headers; no directory listings."""

    _status = 0

    def send_response(self, code, message=None):  # noqa: ANN001 - signature of the base class
        self._status = code
        super().send_response(code, message)

    def end_headers(self) -> None:
        path = self.path.split("?", 1)[0]
        # Only a file that was found is immutable; a 404 under /assets/ must not be cached for a year.
        if self._status == 200 and path.startswith("/assets/") and not path.endswith("/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def list_directory(self, path):  # noqa: ANN001 - signature of the base class
        self.send_error(404, "Not Found")
        return None


class UiServer(ThreadingHTTPServer):
    daemon_threads = True


class UiServer6(UiServer):
    address_family = socket.AF_INET6


def make_server(directory: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> UiServer:
    """A server for ``directory`` on ``host:port`` (IPv6 when ``host`` contains a colon)."""
    server_class = UiServer6 if ":" in host else UiServer
    handler = functools.partial(UiHandler, directory=directory)
    return server_class((host.strip("[]"), port), handler)


def main(argv: Optional[List[str]] = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: serve_ui.py <dist dir>", file=sys.stderr)
        return 2
    directory = os.path.abspath(args[0])
    if not os.path.isfile(os.path.join(directory, "index.html")):
        print(f"serve_ui: {directory}/index.html is missing; build the frontend first", file=sys.stderr)
        return 1
    host = os.environ.get("COUNCIL_HOST", "").strip() or DEFAULT_HOST
    port_text = os.environ.get("COUNCIL_UI_PORT", "").strip() or str(DEFAULT_PORT)
    try:
        port = int(port_text)
    except ValueError:
        print(f"serve_ui: COUNCIL_UI_PORT must be an integer, got {port_text!r}", file=sys.stderr)
        return 2
    with make_server(directory, host, port) as server:
        print(f"serve_ui: {directory} on http://{host}:{port}/", file=sys.stderr, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

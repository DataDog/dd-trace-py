"""Tests for tests/wait-for-services.py."""

import http.server
import os
import pathlib
import subprocess
import sys
import threading

import pytest


_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "wait-for-services.py"


@pytest.fixture
def http_status():
    """Serve a configurable HTTP status on a local port. Yield the state dict and the base URL."""
    state = {"status": 200}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(state["status"])
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _environment_without_proxies():
    # A host proxy setting would send the request for the local server to the proxy.
    return {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}


def _wait(services, agent_url, timeout=30):
    # -S hides site-packages, so the run fails if a check imports a third-party client library.
    return subprocess.run(
        [sys.executable, "-S", str(_SCRIPT_PATH), *services],
        env={**_environment_without_proxies(), "DD_TRACE_AGENT_URL": agent_url},
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.parametrize("status", [200, 404])
def test_agent_check_needs_only_the_standard_library(http_status, status):
    state, url = http_status
    state["status"] = status

    result = _wait(["testagent", "memcached"], url)

    assert result.returncode == 0, result.stderr
    assert "Succeeded: check_agent" in result.stderr


def test_client_library_check_fails_without_retries_when_the_library_is_missing(http_status):
    _, url = http_status

    result = _wait(["postgres"], url)

    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr
    assert "Retrying" not in result.stderr

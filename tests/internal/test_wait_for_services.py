"""Tests for tests/wait-for-services.py."""

import http.server
import os
import pathlib
import socket
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
    # Skip backoff sleeps so failed checks exhaust their real retry budget quickly.
    return subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import pathlib, runpy, sys, time; time.sleep = lambda _: None; "
            "script = sys.argv.pop(1); sys.path.insert(0, str(pathlib.Path(script).parent)); "
            "runpy.run_path(script, run_name='__main__')",
            str(_SCRIPT_PATH),
            *services,
        ],
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

    assert result.returncode == 1
    assert "ModuleNotFoundError" in result.stderr
    assert "Retrying" not in result.stderr


@pytest.mark.parametrize("connection_refused", [False, True])
def test_unhealthy_service_exits_with_retryable_status(http_status, connection_refused):
    state, url = http_status
    state["status"] = 503
    if connection_refused:
        with socket.socket() as closed_service:
            closed_service.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{closed_service.getsockname()[1]}"

    result = _wait(["testagent", "postgres"], url)

    assert result.returncode == 75, result.stderr
    assert "Attempt 99: check_agent" in result.stderr
    assert ("URLError" if connection_refused else "Agent not ready") in result.stderr
    assert "testagent" in result.stderr
    assert "check_postgres" not in result.stderr
    assert "Succeeded" not in result.stderr


def test_no_services_exits_with_usage_error(http_status):
    _, url = http_status

    result = _wait([], url)

    assert result.returncode == 1
    assert "usage:" in result.stdout

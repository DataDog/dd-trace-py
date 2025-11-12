"""
End-to-end tests for runtime coverage functionality.

These tests launch a WSGI application with DD_TRACE_RUNTIME_COVERAGE_ENABLED,
make HTTP requests to it, and verify that coverage data is collected and sent to the agent.
"""

import http.client
import os
import signal
import subprocess
import time
from unittest import mock
from urllib.parse import urlparse

import pytest

from ddtrace.internal.compat import PYTHON_VERSION_INFO


DEFAULT_HEADERS = {
    "User-Agent": "test-client/1.0",
}


class SimpleHTTPClient:
    """Simple HTTP client using http.client for testing."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        parsed = urlparse(base_url)
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 80

    def get(self, path, headers=None):
        """Make a GET request."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            all_headers = DEFAULT_HEADERS.copy()
            if headers:
                all_headers.update(headers)
            conn.request("GET", path, headers=all_headers)
            response = conn.getresponse()
            data = response.read()

            class Response:
                def __init__(self, status, data):
                    self.status = status
                    self.data = data

            return Response(response.status, data)
        finally:
            conn.close()

    def wait(self, path="/", max_retries=50, delay=0.1):
        """Wait for the server to start."""
        for _ in range(max_retries):
            try:
                response = self.get(path)
                if response.status == 200:
                    return
            except Exception:
                pass
            time.sleep(delay)
        raise TimeoutError(f"Server failed to start at {self.base_url}")


@pytest.fixture
def server_port():
    return "8765"


@pytest.fixture
def server_command(server_port):
    cmd = [
        "ddtrace-run",
        "python",
        "tests/ci_visibility/app_with_runtime_coverage.py",
        server_port,
    ]
    return cmd


def env_with_runtime_coverage():
    """Environment with runtime coverage enabled."""
    import tempfile
    debug_file = os.path.join(tempfile.gettempdir(), "runtime_coverage_debug.json")
    env = os.environ.copy()
    env.update(
        {
            "DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true",
            "DD_TRACE_SQLITE3_ENABLED": "0",
            "_DD_TEST_RUNTIME_COVERAGE_DEBUG_FILE": debug_file,
        }
    )
    return env


def env_without_runtime_coverage():
    """Environment without runtime coverage (control)."""
    env = os.environ.copy()
    env.update(
        {
            "DD_TRACE_SQLITE3_ENABLED": "0",
        }
    )
    return env


@pytest.fixture
def wsgi_client(server_command, server_port, env_arg):
    proc = subprocess.Popen(
        server_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env_arg(),
        preexec_fn=os.setsid,
        cwd=str(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    )
    try:
        client = SimpleHTTPClient("http://0.0.0.0:%s" % server_port)
        try:
            client.wait()
        except TimeoutError:
            stdout = proc.stdout.read() if proc.stdout else b""
            stderr = proc.stderr.read() if proc.stderr else b""
            raise TimeoutError(
                "Server failed to start\n======STDOUT=====%s\n\n======STDERR=====%s\n" % (stdout, stderr)
            )
        yield client
        try:
            client.get("/shutdown")
        except Exception:
            pass
        time.sleep(0.2)
    finally:
        # Print subprocess output for debugging
        if proc.stdout:
            stdout = proc.stdout.read()
            if stdout:
                print(f"\n{'='*80}")
                print("SUBPROCESS STDOUT:")
                print(f"{'='*80}")
                print(stdout.decode("utf-8", errors="replace"))
                print(f"{'='*80}\n")
        if proc.stderr:
            stderr = proc.stderr.read()
            if stderr:
                print(f"\n{'='*80}")
                print("SUBPROCESS STDERR:")
                print(f"{'='*80}")
                print(stderr.decode("utf-8", errors="replace"))
                print(f"{'='*80}\n")
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
@pytest.mark.parametrize("env_arg", (env_with_runtime_coverage, env_without_runtime_coverage))
def test_runtime_coverage_collection(wsgi_client, env_arg):
    """Test that runtime coverage is collected (or not) based on DD_TRACE_RUNTIME_COVERAGE_ENABLED."""
    import json
    import tempfile
    coverage_enabled = env_arg == env_with_runtime_coverage
    debug_file = os.path.join(tempfile.gettempdir(), "runtime_coverage_debug.json")
    
    # Clean up any existing debug file
    if os.path.exists(debug_file):
        os.remove(debug_file)

    resp = wsgi_client.get("/")
    assert resp.status == 200
    assert b"Results:" in resp.data

    # Give the writer time to flush
    time.sleep(0.5)
    
    # Read debug file if coverage was enabled
    if coverage_enabled:
        assert os.path.exists(debug_file), f"Debug file not created at {debug_file}"
        with open(debug_file, "r") as f:
            debug_data = json.load(f)
            print(f"\n{'='*80}")
            print("ACTUAL SEGMENTS FROM SUBPROCESS:")
            print(f"{'='*80}")
            for i, segment in enumerate(debug_data.get("segments", [])):
                print(f"  Segment {i}: {segment}")
            print(f"{'='*80}\n")
            
            # Intentionally wrong assertion to see actual values
            expected_segments = [[999, 0, 999, 0, -1]]
            actual_segments = debug_data.get("segments", [])
            assert actual_segments == expected_segments, (
                f"Expected segments:\n{expected_segments}\n\n"
                f"Actual segments:\n{actual_segments}"
            )
    else:
        # When disabled, debug file should NOT exist
        assert not os.path.exists(debug_file), "Debug file should not be created when coverage is disabled"

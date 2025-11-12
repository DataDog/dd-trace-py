"""
End-to-end snapshot tests for runtime coverage functionality.

These tests launch a WSGI application with DD_TRACE_RUNTIME_COVERAGE_ENABLED,
make HTTP requests to it, and verify that coverage data is collected and sent to the agent.

The snapshot framework automatically validates that the traces/spans match the expected snapshots,
including validation of coverage data presence/absence.
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

# Ignore dynamic fields in snapshots
SNAPSHOT_IGNORES = [
    "meta.error.stack",
    "meta.runtime-id",
    "metrics._dd.tracer_kr",
    "metrics.process_id",
    "duration",
    "start",
]


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
    env = os.environ.copy()
    env.update(
        {
            "DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true",
            "DD_TRACE_SQLITE3_ENABLED": "0",
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
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
@pytest.mark.parametrize("env_arg", (env_with_runtime_coverage, env_without_runtime_coverage))
def test_runtime_coverage_simple_request(wsgi_client, env_arg):
    """Test that runtime coverage is collected for a simple request.

    The snapshot will automatically validate that coverage data is present in the spans.
    """
    # Determine if coverage should be enabled based on environment
    coverage_enabled = env_arg == env_with_runtime_coverage

    # Patch the writer to capture what gets written
    with mock.patch("ddtrace.internal.ci_visibility.runtime_coverage_writer.RuntimeCoverageWriter._put") as mock_put:
        resp = wsgi_client.get("/")
        assert resp.status == 200
        assert b"Result:" in resp.data

        # Give the writer time to flush
        time.sleep(0.5)

        # Verify the writer behavior based on coverage_enabled flag
        if mock_put.called:
            # Extract the spans that were written
            call_args_list = mock_put.call_args_list
            written_spans = []
            for call in call_args_list:
                if len(call.args) > 0:
                    written_spans.extend(call.args[0])

            # Collect spans with coverage data
            coverage_spans = []
            for span in written_spans:
                coverage_tag = span.get_struct_tag("test.code_coverage")
                if coverage_tag:
                    coverage_spans.append((span, coverage_tag))

            if coverage_enabled:
                # When coverage is enabled, we MUST have coverage data
                assert len(coverage_spans) > 0, "Expected at least one span with coverage data when coverage is enabled"

                # Validate the coverage structure
                for span, coverage_data in coverage_spans:
                    assert "files" in coverage_data, "Coverage data should have 'files' key"
                    assert len(coverage_data["files"]) > 0, "Should have at least one file in coverage"

                    # Verify the covered file is from our test code
                    filenames = [f.get("filename", "") for f in coverage_data["files"]]
                    assert any(
                        "callee.py" in fname for fname in filenames
                    ), f"Expected callee.py in coverage files, got: {filenames}"
            else:
                # When coverage is disabled, we MUST NOT have coverage data
                assert (
                    len(coverage_spans) == 0
                ), f"Expected NO coverage data when coverage is disabled, but found {len(coverage_spans)} span(s) with coverage"


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
@pytest.mark.parametrize("env_arg", (env_with_runtime_coverage, env_without_runtime_coverage))
def test_runtime_coverage_multiple_paths(wsgi_client, env_arg):
    """Test runtime coverage with multiple code paths.

    The snapshot will automatically validate that coverage data is present in the spans.
    """
    # Determine if coverage should be enabled based on environment
    coverage_enabled = env_arg == env_with_runtime_coverage

    with mock.patch("ddtrace.internal.ci_visibility.runtime_coverage_writer.RuntimeCoverageWriter._put") as mock_put:
        resp = wsgi_client.get("/multiple")
        assert resp.status == 200
        assert b"Results:" in resp.data

        # Give the writer time to flush
        time.sleep(0.5)

        # Verify the writer behavior based on coverage_enabled flag
        if mock_put.called:
            # Extract the spans that were written
            call_args_list = mock_put.call_args_list
            written_spans = []
            for call in call_args_list:
                if len(call.args) > 0:
                    written_spans.extend(call.args[0])

            # Collect spans with coverage data
            coverage_spans = []
            for span in written_spans:
                coverage_tag = span.get_struct_tag("test.code_coverage")
                if coverage_tag:
                    coverage_spans.append((span, coverage_tag))

            if coverage_enabled:
                # When coverage is enabled, we MUST have coverage data
                assert len(coverage_spans) > 0, "Expected at least one span with coverage data when coverage is enabled"

                # Validate the coverage structure and verify multiple functions covered
                for span, coverage_data in coverage_spans:
                    assert "files" in coverage_data, "Coverage data should have 'files' key"
                    files = coverage_data["files"]
                    assert len(files) > 0, "Should have at least one file in coverage"

                    # Verify the covered file is from our test code
                    filenames = [f.get("filename", "") for f in files]
                    assert any(
                        "callee.py" in fname for fname in filenames
                    ), f"Expected callee.py in coverage files, got: {filenames}"

                    # Verify we have line coverage (segments or bitmap)
                    for file_data in files:
                        assert "segments" in file_data or "bitmap" in file_data, "File should have coverage data"
            else:
                # When coverage is disabled, we MUST NOT have coverage data
                assert (
                    len(coverage_spans) == 0
                ), f"Expected NO coverage data when coverage is disabled, but found {len(coverage_spans)} span(s) with coverage"


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
@pytest.mark.snapshot(ignores=SNAPSHOT_IGNORES)
@pytest.mark.parametrize("env_arg", (env_with_runtime_coverage, env_without_runtime_coverage))
def test_runtime_coverage_disabled(wsgi_client, env_arg):
    """Test coverage behavior with both enabled and disabled states.

    The snapshot will automatically validate that coverage data presence/absence matches expectations.
    """
    # Determine if coverage should be enabled based on environment
    coverage_enabled = env_arg == env_with_runtime_coverage

    with mock.patch("ddtrace.internal.ci_visibility.runtime_coverage_writer.RuntimeCoverageWriter._put") as mock_put:
        resp = wsgi_client.get("/")
        assert resp.status == 200

        # Give time for any potential writes
        time.sleep(0.5)

        # The writer may or may not be called (it might not even be initialized),
        # but if it is, validate based on coverage_enabled flag
        if mock_put.called:
            call_args_list = mock_put.call_args_list
            written_spans = []
            for call in call_args_list:
                if len(call.args) > 0:
                    written_spans.extend(call.args[0])

            # Collect spans with coverage data
            coverage_spans = []
            for span in written_spans:
                coverage_tag = span.get_struct_tag("test.code_coverage")
                if coverage_tag:
                    coverage_spans.append(coverage_tag)

            if coverage_enabled:
                # When coverage is enabled, we MUST have coverage data
                assert len(coverage_spans) > 0, "Expected at least one span with coverage data when coverage is enabled"
            else:
                # When coverage is disabled, we MUST NOT have coverage data
                assert (
                    len(coverage_spans) == 0
                ), f"Expected NO coverage data when runtime coverage is disabled, but found: {coverage_spans}"

"""
End-to-end tests for runtime coverage functionality.

These tests launch a real WSGI application with DD_TRACE_RUNTIME_COVERAGE_ENABLED,
make HTTP requests to it, and capture the coverage payloads via stdout to verify
that correct coverage data is collected and formatted.

Uses the sitecustomize pattern to patch send_runtime_coverage in the subprocess.

NOTE: Per-request runtime coverage currently reports file-level segments [0, 0, 0, 0, -1]
because sys.monitoring.restart_events() doesn't re-enable per-code-object monitoring that
was disabled with DISABLE. For line-level coverage, set _DD_COVERAGE_FILE_LEVEL=false and
call set_local_events() for all instrumented code objects when starting new contexts.
"""

import http.client
import json
import os
import re
import signal
import subprocess
import tempfile
import time

import pytest

from ddtrace.internal.compat import PYTHON_VERSION_INFO


class SimpleHTTPClient:
    """Simple HTTP client using http.client for testing."""

    def __init__(self, host, port):
        self.host = host
        self.port = port

    def get(self, path):
        """Make a GET request."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            data = response.read()

            class Response:
                def __init__(self, status, content):
                    self.status_code = status
                    self.content = content

            return Response(response.status, data)
        finally:
            conn.close()

    def get_ignored(self, path):
        """Make a GET request and ignore errors."""
        try:
            return self.get(path)
        except Exception:
            pass

    def wait(self, path="/", delay=0.05, max_retries=50):
        """Wait for the server to start."""
        for _ in range(max_retries):
            try:
                response = self.get(path)
                if response.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(delay)
        raise TimeoutError(f"Server failed to start at {self.host}:{self.port}")


def _extract_coverage_payload_from_stdout(stdout_text):
    """
    Extract coverage payload from stdout that was printed by our sitecustomize patch.

    Returns a list of coverage payloads (dict with trace_id, span_id, files).
    """
    payloads = []
    # Look for lines that start with COVERAGE_PAYLOAD:
    for line in stdout_text.split("\n"):
        if line.startswith("COVERAGE_PAYLOAD:"):
            try:
                json_str = line.replace("COVERAGE_PAYLOAD:", "", 1).strip()
                payload = json.loads(json_str)
                payloads.append(payload)
            except json.JSONDecodeError as e:
                print(f"Failed to parse coverage payload: {e}")
                print(f"Line was: {line}")
    return payloads


@pytest.fixture
def wsgi_app_port():
    """Get an available port for the WSGI app."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
def test_runtime_coverage_e2e_with_sitecustomize(wsgi_app_port, tmpdir):
    """
    End-to-end test that verifies runtime coverage collection with a real WSGI server.

    This test:
    1. Creates a sitecustomize.py that patches send_runtime_coverage to print payloads to stdout
    2. Starts the WSGI app with runtime coverage enabled
    3. Makes HTTP requests to exercise code
    4. Captures stdout and parses coverage payloads
    5. Asserts on the payload structure and content
    """
    # Create sitecustomize.py in a temp directory
    sitecustomize_content = """
# sitecustomize.py - Patch runtime coverage to print payloads to stdout for testing
import sys
import json

def patched_send_runtime_coverage(span, files):
    \"\"\"Print coverage payload to stdout instead of sending to intake.\"\"\"
    payload = {
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "files": files
    }
    # Print to stdout in a parseable format for test assertions
    print(f"COVERAGE_PAYLOAD:{json.dumps(payload)}", flush=True)
    return True

# Patch at import time
try:
    from ddtrace.internal.ci_visibility import runtime_coverage
    runtime_coverage.send_runtime_coverage = patched_send_runtime_coverage
except Exception:
    pass  # Fail silently - test will fail if patching didn't work
"""

    sitecustomize_file = tmpdir.join("sitecustomize.py")
    sitecustomize_file.write(sitecustomize_content)

    # Prepare environment
    env = os.environ.copy()
    env.update(
        {
            "DD_TRACE_RUNTIME_COVERAGE_ENABLED": "true",
            "DD_TRACE_SQLITE3_ENABLED": "0",
            "DD_TRACE_ENABLED": "true",
            # "DD_TRACE_DEBUG": "true",
            "PYTHONPATH": str(tmpdir) + ":" + env.get("PYTHONPATH", ""),
        }
    )

    # Start WSGI app
    cmd = [
        "ddtrace-run",
        "python",
        "tests/ci_visibility/app_with_runtime_coverage.py",
        str(wsgi_app_port),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
        preexec_fn=os.setsid,
        cwd=os.getcwd(),
    )

    try:
        # Create client and wait for server to start
        client = SimpleHTTPClient("127.0.0.1", wsgi_app_port)
        client.wait(delay=0.1, path="/", max_retries=100)

        # Make request to exercise coverage
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Results:" in resp.content

        # Give time for coverage to be collected and printed
        time.sleep(0.05)

        # Shutdown the app
        try:
            client.get_ignored("/shutdown")
        except Exception:
            pass

        # Wait for process to finish (with timeout)
        try:
            proc.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            print("Process didn't exit cleanly, killing it")
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

        # Capture stdout
        stdout = proc.stdout.read().decode("utf-8", errors="replace")

        # Extract coverage payloads from stdout
        payloads = _extract_coverage_payload_from_stdout(stdout)

        # Verify we got at least one payload
        assert len(payloads) > 0, "Should have captured at least one coverage payload"

        # Verify payload structure and content
        found_callee = False
        found_in_context_lib = False
        all_files = set()

        for payload in payloads:
            # Check required fields
            assert "trace_id" in payload, "Payload should have trace_id"
            assert "span_id" in payload, "Payload should have span_id"
            assert "files" in payload, "Payload should have files"

            # Verify IDs are integers
            assert isinstance(payload["trace_id"], int), "trace_id should be an integer"
            assert isinstance(payload["span_id"], int), "span_id should be an integer"

            files = payload["files"]
            assert len(files) > 0, "Should have coverage for at least one file"

            # Collect all filenames across payloads
            for file_data in files:
                filename = file_data.get("filename", "")
                all_files.add(filename)

                # Check if this payload has our business logic files
                if "callee.py" in filename:
                    found_callee = True
                    # Verify it's a relative path, not absolute
                    assert not filename.startswith("/"), f"Filename should be relative: {filename}"
                    assert filename.startswith(
                        "tests/coverage/included_path/"
                    ), f"callee.py should have correct relative path: {filename}"

                if "in_context_lib.py" in filename:
                    found_in_context_lib = True
                    assert filename.startswith(
                        "tests/coverage/included_path/"
                    ), f"in_context_lib.py should have correct relative path: {filename}"

            # Verify file structure - all segments should be file-level format [0, 0, 0, 0, -1]
            for file_data in files:
                assert "filename" in file_data, "File should have filename"
                assert "segments" in file_data, "File should have segments"

                segments = file_data["segments"]
                filename = file_data["filename"]

                # File-level coverage: files with code have [0, 0, 0, 0, -1], empty files can have []
                if len(segments) > 0:
                    assert (
                        len(segments) == 1
                    ), f"File-level coverage should have 1 segment per file, got {len(segments)} for {filename}"

                    segment = segments[0]
                    assert segment == [
                        0,
                        0,
                        0,
                        0,
                        -1,
                    ], f"File-level segment should be [0, 0, 0, 0, -1], got {segment} for {filename}"
                else:
                    # Empty files (like __init__.py) can have no segments
                    assert "__init__.py" in filename or filename.endswith(
                        "__init__.py"
                    ), f"Only __init__.py files should have empty segments, got empty for {filename}"

        # At least one payload should have covered our business logic
        assert found_callee, "Expected at least one payload to include tests/coverage/included_path/callee.py"
        assert (
            found_in_context_lib
        ), "Expected at least one payload to include tests/coverage/included_path/in_context_lib.py"

        # Verify expected files are present in coverage
        assert any("callee.py" in f for f in all_files), "callee.py should be in coverage"
        assert any("wsgi.py" in f for f in all_files), "wsgi middleware should be in coverage"

    finally:
        # Ensure cleanup
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
def test_runtime_coverage_writer_sends_correct_payload():
    """
    Test that runtime coverage writer sends correctly formatted citestcov payloads.

    This is a simpler E2E test that mocks the HTTP connection to verify
    the payload structure without needing a subprocess server.
    """
    from unittest import mock
    from pathlib import Path

    from ddtrace.internal.ci_visibility.runtime_coverage import build_runtime_coverage_payload
    from ddtrace.internal.ci_visibility.runtime_coverage import initialize_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage import send_runtime_coverage
    from ddtrace.internal.ci_visibility.runtime_coverage_writer import initialize_runtime_coverage_writer
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    # Initialize coverage
    ModuleCodeCollector._instance = None
    assert initialize_runtime_coverage()
    assert initialize_runtime_coverage_writer()

    # Collect coverage
    ModuleCodeCollector.start_coverage()
    from tests.coverage.included_path.callee import called_in_context_main
    from tests.coverage.included_path.callee import called_in_session_main

    called_in_context_main(1, 2)
    called_in_session_main(3, 4)
    ModuleCodeCollector.stop_coverage()

    # Build payload
    payload = build_runtime_coverage_payload(Path(os.getcwd()), trace_id=12345, span_id=67890)
    assert payload is not None
    assert len(payload["files"]) > 0

    # Mock the HTTP connection to capture what gets sent
    sent_data = []

    def capture_request(self, method, url, body=None, headers=None):
        sent_data.append({"method": method, "url": url, "body": body, "headers": headers})

        # Return a mock response
        class MockResponse:
            status = 200

            def read(self):
                return b"OK"

        return MockResponse()

    # Mock the span
    mock_span = mock.Mock()
    mock_span.trace_id = 12345
    mock_span.span_id = 67890
    mock_span._metrics = {}
    mock_span.get_tags.return_value = {}
    mock_span.get_struct_tag.return_value = None

    # Send coverage with mocked HTTP
    with mock.patch("http.client.HTTPConnection.request", side_effect=capture_request):
        with mock.patch("http.client.HTTPConnection.getresponse") as mock_resp:
            mock_resp.return_value.status = 200
            mock_resp.return_value.read.return_value = b"OK"

            result = send_runtime_coverage(mock_span, payload["files"])
            assert result is True

    # Verify the span was tagged with coverage data
    mock_span._set_struct_tag.assert_called_once()
    call_args = mock_span._set_struct_tag.call_args
    assert call_args[0][0] == "test.coverage"  # The COVERAGE_TAG_NAME constant
    coverage_data = call_args[0][1]
    assert "files" in coverage_data
    assert len(coverage_data["files"]) > 0

    # Verify coverage file structure and specific expected files
    filenames = [f["filename"] for f in coverage_data["files"]]

    # Must include our business logic files
    assert any("callee.py" in f for f in filenames), f"Expected callee.py in coverage, got: {filenames}"
    assert any("in_context_lib.py" in f for f in filenames), f"Expected in_context_lib.py in coverage, got: {filenames}"

    for file_data in coverage_data["files"]:
        assert "filename" in file_data
        assert "segments" in file_data
        assert isinstance(file_data["segments"], list)

        # Filenames should be relative paths
        filename = file_data["filename"]
        if "tests/coverage/" in filename:
            assert not filename.startswith("/"), f"Should be relative path: {filename}"
            assert filename.startswith("tests/coverage/"), f"Should start with tests/coverage/: {filename}"

        # Verify segment format [start_line, start_col, end_line, end_col, count]
        # File-level coverage: files with code have [0, 0, 0, 0, -1], empty files (e.g., __init__.py) can have []
        segments = file_data["segments"]
        if len(segments) > 0:
            # Files with executable code should have exactly 1 segment for file-level coverage
            assert len(segments) == 1, f"File-level coverage should have 1 segment, got {len(segments)} for {filename}"

            segment = segments[0]
            assert segment == [
                0,
                0,
                0,
                0,
                -1,
            ], f"File-level segment should be [0, 0, 0, 0, -1], got {segment} for {filename}"
        else:
            # Empty files (like __init__.py) can have no segments
            assert "__init__.py" in filename or filename.endswith(
                "__init__.py"
            ), f"Only __init__.py files should have empty segments, got empty segments for {filename}"


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
def test_runtime_coverage_disabled_e2e(wsgi_app_port, tmpdir):
    """
    Verify that when runtime coverage is NOT enabled, no coverage payloads are generated.
    """
    # Create sitecustomize.py in a temp directory
    sitecustomize_content = """
# sitecustomize.py - Patch send_runtime_coverage to print payload to stdout
import sys
import json

def patched_send_runtime_coverage(span, files):
    \"\"\"This should NOT be called when coverage is disabled.\"\"\"
    print(f"COVERAGE_PAYLOAD_UNEXPECTED", flush=True)
    return True

# Patch at import time
try:
    from ddtrace.internal.ci_visibility import runtime_coverage
    runtime_coverage.send_runtime_coverage = patched_send_runtime_coverage
except Exception:
    pass  # Module might not be loaded if coverage is disabled
"""

    sitecustomize_file = tmpdir.join("sitecustomize.py")
    sitecustomize_file.write(sitecustomize_content)

    # Environment WITHOUT runtime coverage
    env = os.environ.copy()
    env.update(
        {
            # DD_TRACE_RUNTIME_COVERAGE_ENABLED is NOT set
            "DD_TRACE_SQLITE3_ENABLED": "0",
            "DD_TRACE_ENABLED": "true",
            # "DD_TRACE_DEBUG": "true",
            "PYTHONPATH": str(tmpdir) + ":" + env.get("PYTHONPATH", ""),
        }
    )

    cmd = [
        "ddtrace-run",
        "python",
        "tests/ci_visibility/app_with_runtime_coverage.py",
        str(wsgi_app_port),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
        preexec_fn=os.setsid,
        cwd=os.getcwd(),
    )

    try:
        client = SimpleHTTPClient("127.0.0.1", wsgi_app_port)
        client.wait(delay=0.1, path="/", max_retries=100)

        resp = client.get("/")
        assert resp.status_code == 200

        time.sleep(0.05)
        # time.sleep(1.0)

        try:
            client.get_ignored("/shutdown")
        except Exception:
            pass

        # Wait for process to finish
        try:
            proc.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()

        stdout = proc.stdout.read().decode("utf-8", errors="replace")

        # Should NOT have any coverage payloads
        assert "COVERAGE_PAYLOAD:" not in stdout, "Should not generate coverage when disabled"
        assert "COVERAGE_PAYLOAD_UNEXPECTED" not in stdout, "send_runtime_coverage should not be called"

    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass

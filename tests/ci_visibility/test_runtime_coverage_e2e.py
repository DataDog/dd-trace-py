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
import signal
import subprocess
import time

import pytest

from ddtrace.internal.compat import PYTHON_VERSION_INFO

PORT = 18765


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


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
def test_runtime_coverage_e2e_with_sitecustomize(tmpdir):
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
import base64
import json

def patched_send_runtime_coverage(span, files):
    \"\"\"Print coverage payload to stdout instead of sending to intake.\"\"\"
    # Convert bitmap bytes to base64 for JSON serialization
    files_serializable = []
    for file_data in files:
        file_copy = file_data.copy()
        if "bitmap" in file_copy and isinstance(file_copy["bitmap"], bytes):
            file_copy["bitmap"] = base64.b64encode(file_copy["bitmap"]).decode("ascii")
        files_serializable.append(file_copy)
    
    payload = {
        "trace_id": span.trace_id,
        "span_id": span.span_id,
        "files": files_serializable
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
            "DD_TRACE_DEBUG": "true",
            "PYTHONPATH": str(tmpdir) + ":" + env.get("PYTHONPATH", ""),
        }
    )

    # Start WSGI app
    cmd = [
        "ddtrace-run",
        "python",
        "tests/ci_visibility/app_with_runtime_coverage.py",
        str(PORT),
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
        client = SimpleHTTPClient("127.0.0.1", PORT)
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

        # Capture stdout and stderr
        stdout = proc.stdout.read().decode("utf-8", errors="replace")
        stderr = proc.stderr.read().decode("utf-8", errors="replace")

        # Extract coverage payloads from stdout
        payloads = _extract_coverage_payload_from_stdout(stdout)

        # Verify we got at least one payload
        assert len(payloads) > 0, (
            f"Should have captured at least one coverage payload.\n"
            f"stdout: {stdout[:500]}\n"
            f"stderr: {stderr[:500]}"
        )

        # Verify payload structure and content
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

            # Verify file structure - bitmap format (new efficient format)
            for file_data in files:
                assert "filename" in file_data, "File should have filename"
                assert "bitmap" in file_data, "File should have bitmap"

                bitmap = file_data["bitmap"]
                filename = file_data["filename"]

                # Bitmap format: raw bytes representing covered lines as a bit array
                # Each bit position represents whether that line number is covered
                assert isinstance(bitmap, (bytes, str)), f"Bitmap should be bytes or base64 string for {filename}"
                if isinstance(bitmap, bytes):
                    assert len(bitmap) > 0, f"Bitmap should not be empty for {filename}"

        # Verify that we captured coverage for the test application and libraries
        # The exact files captured may vary depending on what gets imported, but we should
        # at least have some of the key files that are expected to be exercised
        expected_files_to_include = {
            "tests/coverage/included_path/callee.py",
            "ddtrace/contrib/internal/wsgi/wsgi.py",
        }
        assert expected_files_to_include.issubset(all_files), (
            f"Expected at least {expected_files_to_include} to be covered, got {all_files}"
        )

    finally:
        # Ensure cleanup
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


@pytest.mark.skipif(PYTHON_VERSION_INFO < (3, 12), reason="Requires Python 3.12+")
def test_runtime_coverage_disabled_e2e(tmpdir):
    """
    Verify that when runtime coverage is NOT enabled, no coverage payloads are generated.
    """
    # Create sitecustomize.py in a temp directory
    sitecustomize_content = """
# sitecustomize.py - Patch send_runtime_coverage to print payload to stdout
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
        str(PORT),
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
        client = SimpleHTTPClient("127.0.0.1", PORT)
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

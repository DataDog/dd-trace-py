# Utility functions and context managers for profiling tests that capture HTTP uploads.
#
# Why not use ddapm_test_agent with session tokens?
# libdatadog's ddog_prof_Endpoint_agent(base_url) appends /profiling/v1/input to
# the path, which strips any query params we'd append (e.g. ?test_session_token=...).
# That means we can't use the test agent's session-token routing for profiling. A
# local capture server avoids the problem entirely: each test gets its own port.
from contextlib import contextmanager
import http.server
import os
import socketserver
import threading
import time
from typing import Generator


class _ProfilingCaptureServer:
    """Minimal HTTP server that captures profiling uploads from ddup.

    Provides the same profiling_requests() interface as TestAgentClient so that
    pprof_utils.get_profile_from_agent() and related helpers work unchanged.

    Each instance binds to an OS-assigned free port and runs in a daemon thread.
    """

    def __init__(self) -> None:
        self._captured: list[dict] = []
        self._server: socketserver.TCPServer
        self._thread: threading.Thread
        self.port: int = 0

    def start(self) -> None:
        captured = self._captured

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                # Normalise header keys to lower-case to match TestAgentRequest convention
                captured.append(
                    {
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                        "url": self.path,
                    }
                )
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass  # silence request logs in test output

        # Port 0 -> OS picks an available port
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        server.allow_reuse_address = True
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)

    def profiling_requests(self) -> list[dict]:
        return list(self._captured)

    def clear(self) -> None:
        self._captured.clear()


@contextmanager
def with_profiling_test_agent() -> Generator[_ProfilingCaptureServer, None, None]:
    """Context manager that starts a local capture server and configures ddup to upload to it.

    Two mechanisms route profiler uploads to the capture server:
    - ddup.config_url(): sets _upload_url_override in the ddup module for in-process use.
      Checked once per upload (module variable), no os.environ call on the hot path.
    - _DD_PROFILING_TEST_AGENT_URL env var: inherited by exec-based child processes
      (gunicorn workers, uwsgi). They read it once at module import time.

    Yields a _ProfilingCaptureServer whose profiling_requests() / clear() methods
    mirror the TestAgentClient interface used by pprof_utils helpers.
    """
    from ddtrace.internal.datadog.profiling import ddup

    server = _ProfilingCaptureServer()
    server.start()
    url = f"http://127.0.0.1:{server.port}"
    # Set env var for exec-based child processes (gunicorn workers, uwsgi) that
    # read it once at module import time via _upload_url_override initialisation.
    os.environ["_DD_PROFILING_TEST_AGENT_URL"] = url
    # Call config_url() for in-process use when the compiled binary supports it.
    # Falls back gracefully to env-var-only on older binaries.
    _config_url = getattr(ddup, "config_url", None)
    if _config_url is not None:
        _config_url(url)
    try:
        server.clear()
        yield server
    finally:
        server.clear()
        server.stop()
        if _config_url is not None:
            _config_url(None)
        del os.environ["_DD_PROFILING_TEST_AGENT_URL"]


def get_profile_from_agent(
    client: _ProfilingCaptureServer,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
    assert_samples: bool = True,
):
    """Poll the test agent until at least one profiling upload appears, then return the parsed profile.

    Delegates to pprof_utils.get_profile_from_agent, which is also importable directly
    inside @pytest.mark.subprocess test functions via ``from tests.profiling.collector import pprof_utils``.
    """
    from tests.profiling.collector.pprof_utils import get_profile_from_agent as _impl

    return _impl(client, timeout=timeout, poll_interval=poll_interval, assert_samples=assert_samples)


def get_all_profiles_from_agent(
    client: _ProfilingCaptureServer,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
):
    """Poll the test agent and return all profiling uploads parsed as pprof profiles.

    Waits until at least one upload appears, then returns all of them.
    """
    from tests.profiling.collector.pprof_utils import parse_profile_from_request

    end_time = time.time() + timeout
    while time.time() < end_time:
        reqs = client.profiling_requests()
        if reqs:
            return [parse_profile_from_request(req) for req in reqs]
        time.sleep(poll_interval)

    raise AssertionError(f"No profiling uploads received within {timeout}s")


def get_all_metadata_from_agent(
    client: _ProfilingCaptureServer,
    min_count: int = 1,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> "list[dict]":
    """Poll the test agent and return internal_metadata dicts from all profiling uploads.

    Waits until at least min_count uploads with parseable internal_metadata appear.
    Useful for tests that check metrics like sample_count, greenlet_count, etc.
    """
    from tests.profiling.collector.pprof_utils import parse_internal_metadata_from_request

    end_time = time.time() + timeout
    last_parse_error: str = ""
    while time.time() < end_time:
        reqs = client.profiling_requests()
        if reqs:
            results = []
            parse_errors = []
            for req in reqs:
                try:
                    results.append(parse_internal_metadata_from_request(req))
                except Exception as e:
                    parse_errors.append(str(e))
            if len(results) >= min_count:
                return results
            # Uploads arrived but metadata not parseable -- fail fast with diagnostics
            # rather than timing out after 30s.
            if parse_errors and not results:
                raise AssertionError(
                    f"Got {len(reqs)} profiling upload(s) but could not parse internal_metadata.\n"
                    f"Parse error: {parse_errors[-1]}"
                )
            if parse_errors:
                last_parse_error = parse_errors[-1]
        time.sleep(poll_interval)

    detail = f"\nLast parse error: {last_parse_error}" if last_parse_error else ""
    raise AssertionError(
        f"Expected at least {min_count} profiling upload(s) with internal_metadata within {timeout}s{detail}"
    )

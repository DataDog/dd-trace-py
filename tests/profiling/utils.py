# Utility functions and context managers for profiling tests.
#
# Architecture: a self-contained _ProfilingMiniAgent HTTP server runs per fixture
# invocation.  ddup is configured to upload to
# http://127.0.0.1:{PORT}/session/{TOKEN}/profiling/v1/input
# (libdatadog appends /profiling/v1/input to whatever base URL we give it).
# The mini agent stores uploads keyed by token and serves a query API so that
# _ProfilingMiniAgentClient.profiling_requests() / clear() work the same way as
# TestAgentClient for other test domains.
#
# No external ddapm-test-agent required: profiling tests are self-contained.
import base64
from contextlib import contextmanager
import http.client as _httplib
import http.server
import json
import os
import socketserver
import threading
import time
from typing import Generator
from typing import Optional
import urllib.parse
import uuid


class _ProfilingMiniAgent:
    """Self-contained HTTP server that stores profiling uploads per session token.

    Receives ddup uploads at:
        POST /session/{TOKEN}/profiling/v1/input

    Serves query API:
        GET  /session/{TOKEN}/requests   -> JSON array of stored request dicts
        GET  /session/{TOKEN}/clear      -> 200, clears stored requests for token
        GET  /session/{TOKEN}/start      -> 200, no-op (session compatibility shim)
    """

    def __init__(self) -> None:
        self._requests: "dict[str, list[dict[str, object]]]" = {}
        self._lock = threading.Lock()
        self._server: socketserver.TCPServer
        self._thread: threading.Thread
        self.port: int = 0

    def start(self) -> None:
        agent = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                # Path: /session/{TOKEN}/profiling/v1/input
                parts = self.path.split("/")
                try:
                    session_idx = parts.index("session")
                    token = parts[session_idx + 1]
                    upload_path = "/" + "/".join(parts[session_idx + 2 :])
                except (ValueError, IndexError):
                    token = ""
                    upload_path = self.path

                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)

                if token:
                    with agent._lock:
                        if token not in agent._requests:
                            agent._requests[token] = []
                        agent._requests[token].append(
                            {
                                "headers": {k.lower(): v for k, v in self.headers.items()},
                                # base64-encode for JSON transport; decoded back in the client
                                "body": base64.b64encode(body).decode("ascii"),
                                "url": upload_path,
                            }
                        )

                self.send_response(200)
                self.end_headers()

            def do_GET(self) -> None:
                # Path: /session/{TOKEN}/{action}
                parts = self.path.split("/")
                try:
                    session_idx = parts.index("session")
                    token = parts[session_idx + 1]
                    action = parts[session_idx + 2] if len(parts) > session_idx + 2 else ""
                except (ValueError, IndexError):
                    self.send_response(404)
                    self.end_headers()
                    return

                if action == "requests":
                    with agent._lock:
                        stored = list(agent._requests.get(token, []))
                    payload = json.dumps(stored).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                elif action in ("clear", "start"):
                    if action == "clear":
                        with agent._lock:
                            agent._requests.pop(token, None)
                    self.send_response(200)
                    self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args: object) -> None:
                pass  # silence request logs in test output

        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        server.allow_reuse_address = True
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)


class _ProfilingMiniAgentClient:
    """HTTP client for _ProfilingMiniAgent.

    Implements the profiling_requests() / clear() interface used by the
    get_profile_from_agent helpers, so they work whether called in-process or
    from inside a @pytest.mark.subprocess test body.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url
        self._token = token

    def _request(self, method: str, path: str) -> "tuple[int, bytes]":
        parsed = urllib.parse.urlparse(self._base_url)
        conn = _httplib.HTTPConnection(parsed.hostname or "127.0.0.1", parsed.port, timeout=10)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def profiling_requests(self) -> "list[dict[str, object]]":
        """Return all profiling uploads stored under this session token."""
        status, body = self._request("GET", f"/session/{self._token}/requests")
        if status != 200:
            return []
        stored: "list[dict[str, object]]" = json.loads(body)
        for req in stored:
            if isinstance(req.get("body"), str):
                req["body"] = base64.b64decode(str(req["body"]))
        return stored

    def clear(self) -> None:
        """Clear all stored uploads for this session token."""
        self._request("GET", f"/session/{self._token}/clear")


def _client_from_env() -> _ProfilingMiniAgentClient:
    """Build a _ProfilingMiniAgentClient from env vars set by the parent profiling_test_agent fixture.

    Used by get_profile_from_agent when called without an explicit client inside
    @pytest.mark.subprocess test bodies.
    """
    token = os.environ.get("_DD_PROFILING_TEST_TOKEN", "")
    base_url = os.environ.get("_DD_PROFILING_TEST_PROXY_URL", "http://127.0.0.1:0")
    return _ProfilingMiniAgentClient(base_url=base_url, token=token)


@contextmanager
def with_profiling_test_agent() -> "Generator[_ProfilingMiniAgentClient, None, None]":
    """Context manager that starts a self-contained mini agent and configures ddup to upload to it.

    Generates a unique session token so that concurrent tests are isolated.
    No external ddapm-test-agent required.

    Two mechanisms route profiler uploads to the mini agent:
    - ddup.config_test_token(): sets module variables in ddup for in-process use.
    - _DD_PROFILING_TEST_TOKEN / _DD_PROFILING_TEST_PROXY_URL env vars: inherited by
      exec-based child processes (gunicorn workers, uwsgi) that read them at import time.

    Yields a _ProfilingMiniAgentClient whose profiling_requests() / clear() interface
    is the same as what pprof_utils helpers expect.
    """
    from ddtrace.internal.datadog.profiling import ddup

    token = str(uuid.uuid4())

    server = _ProfilingMiniAgent()
    server.start()
    base_url = f"http://127.0.0.1:{server.port}"

    # Env vars for exec-based child processes that read them once at import time.
    os.environ["_DD_PROFILING_TEST_TOKEN"] = token
    os.environ["_DD_PROFILING_TEST_PROXY_URL"] = base_url

    # In-process: update ddup module vars directly (faster than relying on env-var re-read).
    _config_test_token = getattr(ddup, "config_test_token", None)
    if _config_test_token is not None:
        _config_test_token(token, base_url)

    client = _ProfilingMiniAgentClient(base_url=base_url, token=token)
    try:
        client.clear()
        yield client
    finally:
        client.clear()
        server.stop()
        if _config_test_token is not None:
            _config_test_token(None, None)
        del os.environ["_DD_PROFILING_TEST_TOKEN"]
        del os.environ["_DD_PROFILING_TEST_PROXY_URL"]


def clear_agent_session() -> None:
    """Clear the current test session's uploads from the mini agent.

    Call this inside a subprocess test between upload/query cycles to discard
    previously uploaded profiles so the next get_profile_from_agent() call
    returns only fresh uploads.
    """
    _client_from_env().clear()


def get_all_requests_from_agent(
    client: "Optional[_ProfilingMiniAgentClient]" = None,
    min_count: int = 1,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> "list[dict[str, object]]":
    """Poll the mini agent and return raw request dicts.

    Keeps polling until at least min_count uploads have arrived.
    When called without a client, builds one automatically from env vars set by the parent fixture.
    """
    _client = client or _client_from_env()
    end_time = time.time() + timeout
    reqs: "list[dict[str, object]]" = []
    while time.time() < end_time:
        reqs = _client.profiling_requests()
        if len(reqs) >= min_count:
            return reqs
        time.sleep(poll_interval)

    raise AssertionError(f"Expected at least {min_count} profiling upload(s) within {timeout}s, got {len(reqs)}")


def get_all_profiles_from_agent(
    client: "Optional[_ProfilingMiniAgentClient]" = None,
    min_count: int = 1,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
):
    """Poll the mini agent and return all profiling uploads parsed as pprof profiles.

    Keeps polling until at least min_count uploads have arrived.
    When called without a client, builds one automatically from env vars set by the parent fixture.
    """
    from tests.profiling.collector.pprof_utils import parse_profile_from_request

    reqs = get_all_requests_from_agent(client=client, min_count=min_count, timeout=timeout, poll_interval=poll_interval)
    return [parse_profile_from_request(req) for req in reqs]


def get_all_metadata_from_agent(
    client: "Optional[_ProfilingMiniAgentClient]" = None,
    min_count: int = 1,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> "list[dict[str, object]]":
    """Poll the mini agent and return internal_metadata dicts from all profiling uploads.

    Waits until at least min_count uploads with parseable internal_metadata appear.
    When called without a client, builds one automatically from env vars set by the parent fixture.
    """
    from tests.profiling.collector.pprof_utils import parse_internal_metadata_from_request

    _client = client or _client_from_env()
    end_time = time.time() + timeout
    last_parse_error: str = ""
    while time.time() < end_time:
        reqs = _client.profiling_requests()
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

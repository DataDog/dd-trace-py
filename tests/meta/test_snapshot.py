from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
import time
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import pytest

import ddtrace
from ddtrace.internal.writer import HTTPWriter
from ddtrace.internal.writer import NativeWriter
from tests.utils import TestAgentClient as AgentClient
from tests.utils import snapshot_context


HEADER = "X-Datadog-Test-Session-Token"
HEADERS_ENV = "_DD_TRACE_WRITER_ADDITIONAL_HEADERS"


class Agent(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, list[str]], dict[str, str]]] = []
        self.responses: dict[str, deque[tuple[int, bytes]]] = {}
        self.disconnects = 0
        self.delay = 0.0
        super().__init__(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server_port}"

    def matching(self, path: str) -> list[tuple[str, dict[str, list[str]], dict[str, str]]]:
        return [call for call in self.calls if call[0] == path]


class Handler(BaseHTTPRequestHandler):
    server: Agent

    def do_GET(self) -> None:
        path = urlsplit(self.path)
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if path.path != "/info":
            self.server.calls.append((path.path, parse_qs(path.query), {k.lower(): v for k, v in self.headers.items()}))
        if self.server.disconnects and path.path != "/info":
            self.server.disconnects -= 1
            self.close_connection = True
            return
        if self.server.delay:
            time.sleep(self.server.delay)
        responses = self.server.responses.get(path.path)
        status, body = responses[0] if responses else (200, b'{"rate_by_service": {}}')
        if responses and len(responses) > 1:
            responses.popleft()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_PUT = do_GET
    do_POST = do_GET

    def log_message(self, fmt: str, *args: object) -> None:
        pass


@pytest.fixture
def agent() -> Iterator[Agent]:
    server = Agent()
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


@pytest.fixture
def snapshot_writer(agent: Agent, monkeypatch: pytest.MonkeyPatch) -> Iterator[NativeWriter]:
    writer = NativeWriter(agent.url, sync_mode=True)
    monkeypatch.setattr(ddtrace.tracer._span_aggregator, "writer", writer)
    monkeypatch.setenv("DD_SNAPSHOT_UPDATE", "1")
    try:
        yield writer
    finally:
        writer.shutdown_exporter()


@pytest.mark.parametrize("async_mode", [True, False])
def test_snapshot_compares_once_and_propagates_token(agent, snapshot_writer, async_mode):
    with snapshot_context("example", async_mode=async_mode) as session:
        with ddtrace.tracer.trace("operation"):
            pass
        assert session.token != session.name
        assert snapshot_writer._test_session_token == session.token
    assert len(agent.matching("/test/session/start")) == 1
    assert len(agent.matching("/test/session/snapshot")) == 1
    trace_requests = [call for call in agent.calls if call[0].endswith("/traces")]
    assert len(trace_requests) == 1
    assert trace_requests[0][2][HEADER.lower()] == session.token
    assert agent.calls[0][0] == "/test/session/start"
    assert agent.calls[-1][0] == "/test/session/clear"
    for path in ("start", "snapshot", "clear"):
        assert agent.matching(f"/test/session/{path}")[0][1]["test_session_token"] == [session.token]
    assert snapshot_writer._test_session_token is None


def test_snapshot_attempts_keep_baseline_and_change_token(agent, snapshot_writer):
    tokens = []
    for _ in range(2):
        with snapshot_context("example") as session:
            tokens.append(session.token)
    assert tokens[0] != tokens[1]
    comparisons = agent.matching("/test/session/snapshot")
    assert [call[1]["test_session_token"][0] for call in comparisons] == tokens
    assert [call[1]["file"][0] for call in comparisons] == ["/snapshots/example"] * 2


@pytest.mark.parametrize("headers", [None, "Other:value", f"Other:value,{HEADER}:outer"])
def test_snapshot_restores_exact_headers(agent, snapshot_writer, monkeypatch, headers):
    if headers is None:
        monkeypatch.delenv(HEADERS_ENV, raising=False)
    else:
        monkeypatch.setenv(HEADERS_ENV, headers)
    snapshot_writer.set_test_session_token("original")
    with snapshot_context("example"):
        pass
    assert os.environ.get(HEADERS_ENV) == headers
    assert snapshot_writer._test_session_token == "original"


def test_nested_snapshot_restores_outer_session(agent, snapshot_writer):
    with snapshot_context("outer") as outer:
        outer_headers = os.environ[HEADERS_ENV]
        with snapshot_context("inner") as inner:
            assert inner.token != outer.token
        assert os.environ[HEADERS_ENV] == outer_headers
        assert snapshot_writer._test_session_token == outer.token
    assert len(agent.matching("/test/session/snapshot")) == 2


@pytest.mark.parametrize("outcome", ["failed", "skipped", "xfailed", "fixture_setup_failed"])
def test_unsuccessful_snapshot_does_not_compare(agent, snapshot_writer, outcome):
    expected = {"failed": AssertionError, "skipped": pytest.skip.Exception, "xfailed": pytest.xfail.Exception}
    if outcome == "fixture_setup_failed":
        with snapshot_context("example") as session:
            session.verify_on_exit = False
    else:
        with pytest.raises(expected[outcome]):
            with snapshot_context("example"):
                if outcome == "failed":
                    raise AssertionError("original failure")
                if outcome == "skipped":
                    pytest.skip("skip")
                pytest.xfail("expected")
    assert not agent.matching("/test/session/snapshot")
    assert len(agent.matching("/test/session/clear")) == 1
    assert snapshot_writer._test_session_token is None


@pytest.mark.parametrize("body_fails", [False, True])
def test_clear_failure_restores_state_and_preserves_test_failure(agent, snapshot_writer, monkeypatch, body_fails):
    monkeypatch.setenv(HEADERS_ENV, "Other:value")
    agent.responses["/test/session/clear"] = deque([(503, b"cleanup failed")])
    error = AssertionError if body_fails else RuntimeError
    message = "original failure" if body_fails else "Failed to clear test session"
    with pytest.raises(error, match=message):
        with snapshot_context("example"):
            if body_fails:
                raise AssertionError("original failure")
    assert len(agent.matching("/test/session/clear")) == 1
    assert snapshot_writer._test_session_token is None
    assert os.environ[HEADERS_ENV] == "Other:value"


def test_start_failure_does_not_mutate_state(agent, snapshot_writer, monkeypatch):
    monkeypatch.setenv(HEADERS_ENV, "Other:value")
    agent.responses["/test/session/start"] = deque([(503, b"not ready")])
    with pytest.raises(pytest.fail.Exception, match="not ready"):
        with snapshot_context("example"):
            pytest.fail("body must not execute")
    assert os.environ[HEADERS_ENV] == "Other:value"
    assert snapshot_writer._test_session_token is None
    assert len(agent.calls) == 1


@pytest.mark.parametrize("message", [b"Received unmatched traces: extra", b"Changed resource"])
def test_snapshot_mismatch_is_failure_not_xfail(agent, snapshot_writer, message):
    agent.responses["/test/session/snapshot"] = deque([(400, message)])
    with pytest.raises(pytest.fail.Exception, match=message.decode()):
        with snapshot_context("example"):
            pass
    assert len(agent.matching("/test/session/snapshot")) == 1
    assert snapshot_writer._test_session_token is None


def test_start_disconnect_is_not_retried(agent, snapshot_writer):
    agent.disconnects = 1
    with pytest.raises(RuntimeError, match="Test agent request failed"):
        with snapshot_context("example"):
            pytest.fail("body must not execute")
    assert len(agent.calls) == 1


def test_read_disconnect_reconnects(agent):
    agent.disconnects = 1
    client = AgentClient(agent.url, "attempt")
    status, _ = client._request("GET", client._url("/probe"))
    assert status == 200
    assert len(agent.calls) == 2


def test_transport_timeout_is_bounded(agent):
    agent.delay = 0.2
    client = AgentClient(agent.url)
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="Test agent request failed"):
        client._request("GET", "/probe", timeout=0.03)
    assert time.monotonic() - start < 1


def test_query_values_round_trip(agent):
    client = AgentClient(agent.url, "a&b+c#d space/雪")
    status, _ = client._request("GET", client._url("/probe?existing=yes", ignores="meta.a+b,meta.c&d"))
    assert status == 200
    assert agent.calls[0][1] == {
        "existing": ["yes"],
        "test_session_token": ["a&b+c#d space/雪"],
        "ignores": ["meta.a+b,meta.c&d"],
    }


def test_wait_for_delayed_traces(agent, snapshot_writer):
    agent.responses["/test/session/traces"] = deque([(200, b"[]"), (200, b"[[{}]]")])
    with snapshot_context("example", wait_for_num_traces=1):
        pass
    assert len(agent.matching("/test/session/traces")) == 2
    assert len(agent.matching("/test/session/snapshot")) == 1


@pytest.mark.parametrize(
    "response,exception", [((400, b"bad session"), pytest.fail.Exception), ((200, b"bad JSON"), ValueError)]
)
def test_poll_errors_are_not_swallowed(agent, snapshot_writer, response, exception):
    agent.responses["/test/session/traces"] = deque([response])
    with pytest.raises(exception):
        with snapshot_context("example", wait_for_num_traces=1):
            pass
    assert len(agent.matching("/test/session/traces")) == 1
    assert not agent.matching("/test/session/snapshot")
    assert snapshot_writer._test_session_token is None


def test_wait_timeout_does_not_compare(agent, snapshot_writer):
    agent.responses["/test/session/traces"] = deque([(200, b"[]")])
    with pytest.raises(pytest.fail.Exception, match="expected 1 traces, got 0"):
        with snapshot_context("example", wait_for_num_traces=1, timeout=0.05):
            pass
    assert not agent.matching("/test/session/snapshot")
    assert snapshot_writer._test_session_token is None


@pytest.mark.parametrize("options", [{"timeout": 0}, {"timeout": -1}, {"wait_for_num_traces": -1}])
def test_invalid_wait_configuration(agent, snapshot_writer, options):
    with pytest.raises(ValueError):
        with snapshot_context("example", **options):
            pass
    assert not agent.calls


def test_missing_baseline_fails_with_no_output(agent, snapshot_writer, monkeypatch):
    monkeypatch.delenv("DD_SNAPSHOT_UPDATE")
    with pytest.raises(pytest.fail.Exception, match="Missing snapshot baseline"):
        with snapshot_context("missing-baseline"):
            pass
    assert not agent.matching("/test/session/snapshot")


@pytest.mark.parametrize("suffix", ["", "_tracestats", "_otlp_traces"])
def test_explicit_empty_baseline(agent, snapshot_writer, monkeypatch, tmp_path, suffix):
    from tests import utils

    monkeypatch.delenv("DD_SNAPSHOT_UPDATE")
    monkeypatch.setattr(utils, "FILE_PATH", tmp_path)
    (tmp_path / "snapshots").mkdir()
    (tmp_path / "snapshots" / f"example{suffix}.json").write_text("[]")
    with snapshot_context("example"):
        pass
    assert len(agent.matching("/test/session/snapshot")) == 1


def test_flush_failure_restores_state(agent, snapshot_writer, monkeypatch):
    with pytest.raises(RuntimeError, match="delivery failed"):
        with snapshot_context("example"):

            def fail_flush(**kwargs):
                raise RuntimeError("delivery failed")

            monkeypatch.setattr(snapshot_writer, "flush_queue", fail_flush)
    assert snapshot_writer._test_session_token is None
    assert not agent.matching("/test/session/snapshot")


def test_cleanup_failure_preserves_test_failure(agent, snapshot_writer, monkeypatch, caplog):
    with pytest.raises(AssertionError, match="original failure"):
        with snapshot_context("example"):

            def fail_flush(**kwargs):
                raise RuntimeError("delivery failed")

            monkeypatch.setattr(snapshot_writer, "flush_queue", fail_flush)
            raise AssertionError("original failure")
    assert "cleanup flush failed" in caplog.text
    assert snapshot_writer._test_session_token is None


@pytest.mark.parametrize("mode", ["sync", "asyncio", "threads", "gevent", "fork", "otlp"])
def test_subprocess_gets_token_without_rebuilding_parent(agent, snapshot_writer, mode):
    if mode == "gevent":
        pytest.importorskip("gevent")
    if mode == "fork" and not hasattr(os, "fork"):
        pytest.skip("requires fork")
    setup = ""
    work = "tracer.trace('child').finish()"
    if mode == "asyncio":
        work = """import asyncio
async def work():
    await asyncio.sleep(0.01)
    tracer.trace('child').finish()
asyncio.run(work())"""
    elif mode == "threads":
        work = """from threading import Thread
t = Thread(target=lambda: tracer.trace('child').finish())
t.start()
t.join()"""
    elif mode == "gevent":
        setup = "from gevent import monkey; monkey.patch_all()\n"
        work = "import gevent\ngevent.joinall([gevent.spawn(lambda: tracer.trace('child').finish())], raise_error=True)"
    elif mode == "fork":
        work = """import os
pid = os.fork()
if pid == 0:
    tracer.trace('child').finish()
    tracer.shutdown()
    os._exit(0)
assert os.waitpid(pid, 0)[1] == 0"""
    code = setup + "from ddtrace import tracer\n" + work + "\ntracer.shutdown()"
    exporter = snapshot_writer._exporter
    with snapshot_context("example", subprocess_mode=True) as session:
        env = os.environ.copy()
        env["DD_TRACE_AGENT_URL"] = agent.url
        env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "false"
        if mode == "otlp":
            env.update(
                OTEL_TRACES_EXPORTER="otlp",
                OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/protobuf",
                OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=agent.url + "/v1/traces",
            )
        child = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            timeout=20,
        )
        assert child.returncode == 0, child.stderr
        assert snapshot_writer._exporter is exporter
        assert snapshot_writer._test_session_token is None
    trace_requests = [call for call in agent.calls if call[0].endswith("/traces")]
    assert trace_requests
    assert all(call[2][HEADER.lower()] == session.token for call in trace_requests)
    assert snapshot_writer._exporter is exporter


def test_explicit_snapshot_directory(agent, snapshot_writer, monkeypatch):
    monkeypatch.setenv("DD_TEST_AGENT_SNAPSHOT_DIR", "/custom/snapshots")
    with snapshot_context("example"):
        pass
    assert agent.matching("/test/session/snapshot")[0][1]["file"] == ["/custom/snapshots/example"]


@pytest.mark.parametrize("outcome", ["pass", "fail", "skip", "xfail", "setup_error"])
def test_snapshot_fixture_outcomes(agent, monkeypatch, testdir, outcome):
    monkeypatch.setenv("DD_TRACE_AGENT_URL", agent.url)
    monkeypatch.setenv("DD_INSTRUMENTATION_TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("DD_SNAPSHOT_UPDATE", "1")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[2]))
    monkeypatch.delenv(HEADERS_ENV, raising=False)
    testdir.makeconftest(
        """
import pytest
from tests.conftest import snapshot

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    setattr(item, "rep_" + report.when, report)

@pytest.fixture
def broken():
    raise RuntimeError("fixture setup failed")
"""
    )
    body = {
        "pass": "pass",
        "fail": "assert False, 'original failure'",
        "skip": "pytest.skip('skip')",
        "xfail": "pytest.xfail('expected')",
        "setup_error": "pass",
    }[outcome]
    argument = "broken" if outcome == "setup_error" else ""
    testdir.makepyfile(
        f"""
import os
import pytest
import ddtrace

@pytest.mark.snapshot(token="example")
def test_case({argument}):
    {body}

def test_restored():
    assert "{HEADERS_ENV}" not in os.environ
    assert ddtrace.tracer._span_aggregator.writer._test_session_token is None
"""
    )
    result = testdir.runpytest_subprocess("-q", "-p", "no:randomly", "--no-cov")
    expected = {
        "pass": {"passed": 2},
        "fail": {"passed": 1, "failed": 1},
        "skip": {"passed": 1, "skipped": 1},
        "xfail": {"passed": 1, "xfailed": 1},
        "setup_error": {"passed": 1, "errors": 1},
    }
    result.assert_outcomes(**expected[outcome])
    assert len(agent.matching("/test/session/start")) == 1
    assert len(agent.matching("/test/session/snapshot")) == (1 if outcome == "pass" else 0)


@pytest.mark.parametrize("body_fails", [True, False])
def test_writer_restore_failure_preserves_primary_error(agent, snapshot_writer, monkeypatch, body_fails):
    set_token = snapshot_writer.set_test_session_token

    def fail_restore(token):
        if token is None:
            raise RuntimeError("restore failed")
        set_token(token)

    monkeypatch.setattr(snapshot_writer, "set_test_session_token", fail_restore)
    exception = AssertionError if body_fails else RuntimeError
    message = "original failure" if body_fails else "restore failed"
    with pytest.raises(exception, match=message):
        with snapshot_context("example"):
            if body_fails:
                raise AssertionError("original failure")


def test_disabled_snapshots_are_skipped(agent, snapshot_writer, monkeypatch):
    monkeypatch.setenv("DD_SNAPSHOT_ENABLED", "0")
    with pytest.raises(pytest.skip.Exception, match="Snapshot verification is disabled"):
        with snapshot_context("example"):
            pytest.fail("body must not execute")
    assert not agent.matching("/test/session/start")


def test_subprocess_capture_drains_parent_before_start(agent, snapshot_writer):
    snapshot_writer._sync_mode = False
    with ddtrace.tracer.trace("previous-test"):
        pass
    with snapshot_context("example", subprocess_mode=True):
        pass
    paths = [call[0] for call in agent.calls]
    trace_index = next(i for i, path in enumerate(paths) if path.endswith("/traces"))
    assert trace_index < paths.index("/test/session/start")
    assert HEADER.lower() not in agent.calls[trace_index][2]
    snapshot_writer.stop()


@pytest.mark.parametrize("snapshot_first", [True, False])
def test_legacy_snapshot_decorator_with_subprocess(agent, monkeypatch, testdir, snapshot_first):
    monkeypatch.setenv("DD_TRACE_AGENT_URL", agent.url)
    monkeypatch.setenv("DD_INSTRUMENTATION_TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("DD_SNAPSHOT_UPDATE", "1")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).parents[2]))
    testdir.makeconftest(
        """
import pytest
from tests.conftest import snapshot, pytest_collection_modifyitems, pytest_pyfunc_call

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    setattr(item, "rep_" + report.when, report)
"""
    )
    decorators = ["@snapshot()", "@pytest.mark.subprocess()"]
    if not snapshot_first:
        decorators.reverse()
    testdir.makepyfile(
        "import pytest\nfrom tests.utils import snapshot\n"
        + "\n".join(decorators)
        + """
def test_case():
    from ddtrace import tracer
    tracer.trace("child").finish()
    tracer.shutdown()
"""
    )
    result = testdir.runpytest_subprocess("-q", "-p", "no:randomly", "--no-cov")
    result.assert_outcomes(passed=1)
    assert len(agent.matching("/test/session/start")) == 1
    assert len(agent.matching("/test/session/snapshot")) == 1
    token = agent.matching("/test/session/start")[0][1]["test_session_token"][0]
    traces = [call for call in agent.calls if call[0].endswith("/traces")]
    assert traces and all(call[2][HEADER.lower()] == token for call in traces)


def test_session_start_failure_survives_optimized_python(agent):
    agent.responses["/test/session/start"] = deque([(503, b"not ready")])
    env = dict(os.environ, DD_TRACE_AGENT_URL=agent.url, DD_INSTRUMENTATION_TELEMETRY_ENABLED="false")
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            "from tests.utils import snapshot_context\nwith snapshot_context('example'): pass",
        ],
        env=env,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode != 0
    assert b"not ready" in result.stderr
    assert not agent.matching("/test/session/snapshot")


class SnapshotHTTPWriter(HTTPWriter):
    def recreate(self, appsec_enabled: bool | None = None, llmobs_enabled: bool | None = None) -> HTTPWriter:
        raise AssertionError("Snapshot capture must not recreate HTTP writers")


@pytest.mark.parametrize("flush_fails", [False, True])
def test_http_writer_restores_headers_and_surfaces_flush_errors(agent, monkeypatch, flush_fails):
    writer = SnapshotHTTPWriter(agent.url, clients=[], headers={"Other": "value"})
    monkeypatch.setattr(ddtrace.tracer._span_aggregator, "writer", writer)
    monkeypatch.setenv("DD_SNAPSHOT_UPDATE", "1")

    def fail_flush(raise_exc=False):
        if raise_exc:
            raise RuntimeError("delivery failed")

    def capture():
        with snapshot_context("example") as session:
            assert writer._headers[HEADER] == session.token
            if flush_fails:
                monkeypatch.setattr(writer, "flush_queue", fail_flush)

    if flush_fails:
        with pytest.raises(RuntimeError, match="delivery failed"):
            capture()
    else:
        capture()
    assert writer._headers == {"Other": "value"}
    assert len(agent.matching("/test/session/snapshot")) == (0 if flush_fails else 1)

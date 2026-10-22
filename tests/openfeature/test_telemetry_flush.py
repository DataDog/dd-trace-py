"""Invocation barriers cover queues, dequeued events, and endpoint acknowledgements."""

from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
import os
import threading
import time
from unittest import mock
from weakref import WeakSet

import pytest

from ddtrace.internal import forksafe
from ddtrace.internal.openfeature import _telemetry
from ddtrace.internal.openfeature._flagevaluation_writer import FlagEvaluationWriter
from ddtrace.internal.openfeature.writer import ExposureWriter
from tests.openfeature.test_flagevaluation_writer import _make_event
from tests.openfeature.test_flagevaluation_writer import _route_selector


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.setattr(_telemetry, "_writers", WeakSet())
    coordinator = forksafe.ResetObject(_telemetry._Coordinator)
    monkeypatch.setattr(_telemetry, "_coordinator", coordinator)
    yield
    if coordinator.worker is not None:
        coordinator.worker.stop()
        coordinator.signal.set()
        coordinator.worker.join(2)


def make_writer(kind="evaluation", status=202):
    connection = mock.Mock()
    connection.getresponse.return_value.status = status
    factory = mock.Mock(return_value=connection)
    if kind == "evaluation":
        writer = FlagEvaluationWriter(route_selector=_route_selector(), connection_factory=factory)
    else:
        writer = ExposureWriter(enabled=True, route_selector=_route_selector(), connection_factory=factory)
    return writer, connection


def enqueue(writer):
    writer.enqueue(_make_event() if isinstance(writer, FlagEvaluationWriter) else {"timestamp": 1})


@pytest.mark.parametrize("kind", ["evaluation", "exposure"])
def test_flush_and_warm_reuse(kind):
    writer, connection = make_writer(kind)
    assert _telemetry.flush(1) == "completed"
    assert _telemetry._coordinator.worker is None
    worker = None
    for _ in range(3):
        enqueue(writer)
        assert _telemetry.flush(1) == "completed"
        if worker is not None:
            assert _telemetry._coordinator.worker is worker
        worker = _telemetry._coordinator.worker
    assert connection.request.call_count == 3
    assert writer._progress.accepted == writer._progress.settled == 3


@pytest.mark.parametrize("pre_drain", [False, True])
@pytest.mark.parametrize("include_healthy", [False, True])
def test_aggregation_failure_settles_and_later_flush_recovers(pre_drain, include_healthy):
    class UnhashableString(str):
        __hash__ = None

    writer, connection = make_writer()
    writer.enqueue(_make_event(flag_key=UnhashableString("my-flag")))
    if include_healthy:
        enqueue(writer)
    if pre_drain:
        # The background drain worker may consume the bad event before flush.
        writer._drain_queue()
    assert _telemetry.flush(1) == "failed"
    assert writer._progress.accepted == writer._progress.settled == 1 + include_healthy
    assert connection.request.call_count == int(include_healthy)

    enqueue(writer)
    assert _telemetry.flush(1) == "completed"
    assert writer._progress.accepted == writer._progress.settled == 2 + include_healthy
    assert connection.request.call_count == 1 + include_healthy
    assert _telemetry.flush(1) == "completed"
    assert connection.request.call_count == 1 + include_healthy


@pytest.mark.parametrize("kind", ["evaluation", "exposure"])
@pytest.mark.parametrize("status", [202, 400, 500])
def test_endpoint_acknowledgement(kind, status):
    writer, connection = make_writer(kind, status)
    enqueue(writer)
    assert _telemetry.flush(1) == ("completed" if status == 202 else "failed")
    assert connection.request.call_count == 1


def test_multiple_providers_and_shared_exposure_writer():
    writers = [make_writer()[0], make_writer()[0], make_writer("exposure")[0]]
    for writer in writers:
        enqueue(writer)
    assert _telemetry.flush(1) == "completed"
    assert all(writer._progress.settled == 1 for writer in writers)


def test_dequeued_event_is_part_of_barrier():
    writer, connection = make_writer()
    entered, release = threading.Event(), threading.Event()
    aggregate = writer._aggregate

    def blocked(event):
        entered.set()
        assert release.wait(2)
        aggregate(event)

    enqueue(writer)
    with mock.patch.object(writer, "_aggregate", side_effect=blocked):
        thread = threading.Thread(target=writer._drain_queue)
        thread.start()
        try:
            assert entered.wait(1)
            assert writer._queue.empty()
            assert _telemetry.flush(0.02) == "timed_out"
        finally:
            release.set()
            thread.join(2)
    assert _telemetry.flush(1) == "completed"
    assert connection.request.call_count == 1


@pytest.mark.parametrize("kind", ["evaluation", "exposure"])
@pytest.mark.parametrize("status", [202, 500])
def test_periodic_inflight_is_not_empty_success(kind, status):
    writer, connection = make_writer(kind, status)
    entered, release = threading.Event(), threading.Event()

    def blocked_response():
        entered.set()
        assert release.wait(2)
        return mock.Mock(status=status)

    connection.getresponse.side_effect = blocked_response
    enqueue(writer)
    thread = threading.Thread(target=writer.periodic)
    thread.start()
    timer = threading.Timer(0.08, release.set)
    try:
        assert entered.wait(1)
        timer.start()
        assert _telemetry.flush(1) == ("completed" if status == 202 else "failed")
    finally:
        release.set()
        thread.join(2)
        timer.cancel()
    assert connection.request.call_count == 1


@pytest.mark.parametrize("stage", ["discovery", "send", "aggregation", "flush_lock", "progress_lock"])
def test_deadline_and_repeated_timeouts_do_not_accumulate_workers(stage):
    writer, connection = make_writer()
    entered, release = threading.Event(), threading.Event()

    def block(*args):
        entered.set()
        assert release.wait(3)

    if stage == "discovery":
        original = writer._route_selector.select
        patch = mock.patch.object(writer._route_selector, "select", side_effect=lambda: (block(), original())[1])
    elif stage == "send":
        patch = mock.patch.object(connection, "getresponse", side_effect=lambda: (block(), mock.Mock(status=202))[1])
    elif stage == "aggregation":
        original = writer._aggregate
        patch = mock.patch.object(writer, "_aggregate", side_effect=lambda e: (block(), original(e))[1])
    else:
        patch = mock.patch.object(writer, "_timeout", 2)
    enqueue(writer)
    lock = writer._flush_lock if stage == "flush_lock" else writer._progress.lock if stage == "progress_lock" else None
    if lock is not None:
        lock.acquire()
    with patch:
        try:
            workers = set()
            for _ in range(4):
                started = time.monotonic()
                assert _telemetry.flush(0.02) == "timed_out"
                assert time.monotonic() - started < 0.15
                workers.add(id(_telemetry._coordinator.worker))
            assert len(workers) == 1
        finally:
            release.set()
            if lock is not None:
                lock.release()
        assert _telemetry.flush(1) == "completed"
    assert connection.request.call_count == 1


def test_disabled_and_zero_budget():
    writer = ExposureWriter(enabled=False)
    enqueue(writer)
    assert _telemetry.flush(0) == "timed_out"
    assert _telemetry.flush(1) == "completed"
    assert _telemetry._coordinator.worker is None


def test_failure_does_not_poison_next_invocation():
    writer, connection = make_writer(status=500)
    enqueue(writer)
    assert _telemetry.flush(1) == "failed"
    connection.getresponse.return_value.status = 202
    enqueue(writer)
    assert _telemetry.flush(1) == "completed"
    assert connection.request.call_count == 2


@pytest.mark.parametrize("kind", ["evaluation", "exposure"])
def test_real_agent_endpoint_receives_before_return(kind):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    selector = _route_selector()
    selector._agent_url = "http://127.0.0.1:%d" % server.server_port
    writer = (
        FlagEvaluationWriter(route_selector=selector)
        if kind == "evaluation"
        else ExposureWriter(enabled=True, route_selector=selector)
    )
    try:
        enqueue(writer)
        assert _telemetry.flush(1) == "completed"
        assert len(received) == 1
        assert received[0][0].endswith("/flagevaluation" if kind == "evaluation" else "/exposures")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("kind", ["evaluation", "exposure"])
def test_agentless_direct_route_uses_same_barrier(kind):
    from ddtrace.internal.settings.openfeature import AGENTLESS

    writer, connection = make_writer(kind)
    writer._route_selector = _route_selector(source=AGENTLESS, endpoints=(), api_key="test-key")
    enqueue(writer)
    assert _telemetry.flush(1) == "completed"
    assert connection.request.call_count == 1
    assert connection.request.call_args.args[1].startswith("/api/v2/")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.parametrize("kind", ["evaluation", "exposure"])
def test_fork_child_does_not_flush_parent_work(kind):
    writer, connection = make_writer(kind)
    enqueue(writer)
    assert _telemetry.flush(1) == "completed"
    connection.reset_mock()
    enqueue(writer)
    pid = os.fork()
    if pid == 0:
        try:
            assert _telemetry.flush(1) == "completed"
            assert connection.request.call_count == 0
            assert writer._progress.accepted == 0
            os._exit(0)
        except BaseException:
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    assert _telemetry.flush(1) == "completed"
    assert connection.request.call_count == 1


@pytest.mark.parametrize("kind", ["evaluation", "exposure"])
def test_ambiguous_send_failure_is_not_retried(kind):
    from ddtrace.internal.settings.openfeature import AGENTLESS

    writer, connection = make_writer(kind)
    writer._route_selector = _route_selector(source=AGENTLESS, api_key="test-key")
    connection.getresponse.side_effect = TimeoutError("response lost after send")
    enqueue(writer)
    assert _telemetry.flush(1) == "failed"
    assert _telemetry.flush(1) == "completed"
    assert connection.request.call_count == 1

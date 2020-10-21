import os
import logging
import math
import mock
import subprocess
import sys
import types
from unittest import TestCase, skip, skipUnless

import ddtrace
from ddtrace import Tracer, tracer
from ddtrace.api import API, Response
from ddtrace.ext import http
from ddtrace.filters import FilterRequestsOnUrl
from ddtrace.constants import FILTERS_KEY
from ddtrace.encoding import JSONEncoder, MsgpackEncoder
from ddtrace.compat import httplib, PYTHON_INTERPRETER, PYTHON_VERSION
from ddtrace.internal.runtime.container import CGroupInfo
from ddtrace.payload import Payload
from ddtrace.span import Span
from tests.tracer.test_tracer import get_dummy_tracer


class MockedLogHandler(logging.Handler):
    """Record log messages to verify error logging logic"""

    def __init__(self, *args, **kwargs):
        self.messages = {"debug": [], "info": [], "warning": [], "error": [], "critical": []}
        super(MockedLogHandler, self).__init__(*args, **kwargs)

    def emit(self, record):
        self.acquire()
        try:
            self.messages[record.levelname.lower()].append(record.getMessage())
        finally:
            self.release()


@skipUnless(
    os.environ.get("TEST_DATADOG_INTEGRATION", False),
    "You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable",
)
class TestWorkers(TestCase):
    """
    Ensures that a workers interacts correctly with the main thread. These are part
    of integration tests so real calls are triggered.
    """

    def setUp(self):
        """
        Create a tracer with running workers, while spying the ``_put()`` method to
        keep trace of triggered API calls.
        """
        # create a new tracer
        self.tracer = Tracer()
        # spy the send() method
        self.writer = self.tracer.writer
        self.writer._put = mock.Mock(self.writer._put, wraps=self.writer._put)

    def tearDown(self):
        """
        Stop running worker
        """
        self._wait_thread_flush()

    def _wait_thread_flush(self):
        """
        Helper that waits for the thread flush
        """
        self.tracer.writer.flush_queue()
        self.tracer.writer.stop()
        self.tracer.writer.join(None)

    def _get_endpoint_payload(self, calls):
        """
        Helper to retrieve the endpoint call from a concurrent
        trace or service call.
        """
        for call, _ in calls:
            return self.writer._encoder._decode(call[0])
        return None

    @skipUnless(
        os.environ.get("TEST_DATADOG_INTEGRATION_UDS", False),
        "You should have a running trace agent on a socket and set TEST_DATADOG_INTEGRATION_UDS=1 env variable",
    )
    def test_worker_single_trace_uds(self):
        self.tracer.configure(uds_path="/tmp/ddagent/trace.sock")
        # Write a first trace so we get a _worker
        self.tracer.trace("client.testing").finish()
        with mock.patch("ddtrace.internal.writer.log") as log_mock:
            self.tracer.trace("client.testing").finish()

            # one send is expected
            self._wait_thread_flush()
            # Check that no error was logged
            assert log_mock.error.call_count == 0

    def test_worker_single_trace_uds_wrong_socket_path(self):
        self.tracer.configure(uds_path="/tmp/ddagent/nosockethere")
        # Write a first trace so we get a _worker
        self.tracer.trace("client.testing").finish()
        with mock.patch("ddtrace.internal.writer.log") as log_mock:
            self.tracer.trace("client.testing").finish()

            # one send is expected
            self._wait_thread_flush()
        log_mock.error.call_count == 1

    def test_worker_single_trace(self):
        # create a trace block and send it using the transport system
        tracer = self.tracer
        tracer.trace("client.testing").finish()

        # one send is expected
        self._wait_thread_flush()
        assert self.writer._put.call_count == 1
        assert self.writer._endpoint == "/v0.4/traces"
        # check and retrieve the right call
        payload = self._get_endpoint_payload(self.writer._put.call_args_list)
        assert len(payload) == 1
        assert len(payload[0]) == 1
        assert payload[0][0][b"name"] == b"client.testing"


@skipUnless(
    os.environ.get("TEST_DATADOG_INTEGRATION", False),
    "You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable",
)
class TestAPITransport(TestCase):
    """
    Ensures that traces are properly sent to a local agent. These are part
    of integration tests so real calls are triggered and you have to execute
    a real trace-agent to let them pass.
    """

    @mock.patch("ddtrace.internal.runtime.container.get_container_info")
    def setUp(self, get_container_info):
        """
        Create a tracer without workers, while spying the ``send()`` method
        """
        # Mock the container id we use for making requests
        get_container_info.return_value = CGroupInfo(container_id="test-container-id")

        # create a new API object to test the transport using synchronous calls
        self.tracer = get_dummy_tracer()
        self.api_json = API("localhost", 8126, encoder=JSONEncoder())
        self.api_msgpack = API("localhost", 8126, encoder=MsgpackEncoder())

    def test_send_many_traces(self):
        # register a single trace with a span and send them to the trace agent
        self.tracer.trace("client.testing").finish()
        trace = self.tracer.writer.pop()
        # 20k is a right number to have both json and msgpack send 2 payload :)
        traces = [trace] * 20000

        self._send_traces_and_check(traces, 2)

    def test_send_single_trace_multiple_spans(self):
        # register some traces and send them to the trace agent
        with self.tracer.trace("client.testing"):
            self.tracer.trace("client.testing").finish()
        trace = self.tracer.writer.pop()
        traces = [trace]

        self._send_traces_and_check(traces)

    def test_send_multiple_traces_multiple_spans(self):
        # register some traces and send them to the trace agent
        with self.tracer.trace("client.testing"):
            self.tracer.trace("client.testing").finish()
        trace_1 = self.tracer.writer.pop()

        with self.tracer.trace("client.testing"):
            self.tracer.trace("client.testing").finish()
        trace_2 = self.tracer.writer.pop()

        traces = [trace_1, trace_2]

        self._send_traces_and_check(traces)


@skipUnless(
    os.environ.get("TEST_DATADOG_INTEGRATION", False),
    "You should have a running trace agent and set TEST_DATADOG_INTEGRATION=1 env variable",
)
class TestRateByService(TestCase):
    """
    Check we get feedback from the agent and we're able to process it.
    """

    def setUp(self):
        """
        Create a tracer without workers, while spying the ``send()`` method
        """
        # create a new API object to test the transport using synchronous calls
        self.tracer = get_dummy_tracer()
        self.api_json = API("localhost", 8126, encoder=JSONEncoder(), priority_sampling=True)
        self.api_msgpack = API("localhost", 8126, encoder=MsgpackEncoder(), priority_sampling=True)

    def test_send_single_trace(self):
        # register a single trace with a span and send them to the trace agent
        self.tracer.trace("client.testing").finish()
        trace = self.tracer.writer.pop()
        traces = [trace]

        # [TODO:christian] when CI has an agent that is able to process the v0.4
        # endpoint, add a check to:
        # - make sure the output is a valid JSON
        # - make sure the priority sampler (if enabled) is updated

        # test JSON encoder
        responses = self.api_json.send_traces(traces)
        assert len(responses) == 1
        assert responses[0].status == 200
        assert responses[0].get_json() == dict(rate_by_service={"service:,env:": 1})

        # test Msgpack encoder
        responses = self.api_msgpack.send_traces(traces)
        assert len(responses) == 1
        assert responses[0].status == 200
        assert responses[0].get_json() == dict(rate_by_service={"service:,env:": 1})


def test_configure_keeps_api_hostname_and_port(self):
    """
    Ensures that when calling configure without specifying hostname and port,
    previous overrides have been kept.
    """
    tracer = Tracer()  # use real tracer with real api
    assert "localhost" == tracer.writer._hostname
    assert 8126 == tracer.writer._port
    tracer.configure(hostname="127.0.0.1", port=8127)
    assert "127.0.0.1" == tracer.writer._hostname
    assert 8127 == tracer.writer._port
    tracer.configure(priority_sampling=True)
    assert "127.0.0.1" == tracer.writer._hostname
    assert 8127 == tracer.writer._port


def test_debug_mode():
    p = subprocess.Popen(
        [sys.executable, "-c", "import ddtrace"],
        env=dict(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.stdout.read() == b""
    assert b"DEBUG:ddtrace" not in p.stderr.read()

    p = subprocess.Popen(
        [sys.executable, "-c", "import ddtrace"],
        env=dict(DD_TRACE_DEBUG="true"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    assert p.stdout.read() == b""
    # Stderr should have some debug lines
    assert b"DEBUG:ddtrace" in p.stderr.read()


def test_payload_too_large():
    t = Tracer()
    # 100000 * 100 = ~10MB
    with mock.patch("ddtrace.internal.writer.log") as log:
        for i in range(100000):
            with t.trace("operation") as s:
                s.set_tag("a" * 10, "b" * 90)

        t.shutdown()
        assert log.warning.call_count > 0
        calls = [
            mock.call("trace buffer is full, dropping trace"),
        ]
        log.warning.assert_has_calls(calls)


def test_large_payload():
    t = Tracer()
    # 100000 * 20 = ~2MB + additional tags
    with mock.patch("ddtrace.internal.writer.log") as log:
        for i in range(100000):
            with t.trace("operation") as s:
                s.set_tag("a" * 1, "b" * 19)

        t.shutdown()
        assert log.warning.call_count == 0


def test_child_spans():
    t = Tracer()
    with mock.patch("ddtrace.internal.writer.log") as log:
        spans = []
        for i in range(100000):
            span = t.trace("op")
        for s in spans:
            s.finish()

        t.shutdown()
        assert log.warning.call_count == 0


def test_single_trace_too_large():
    t = Tracer()
    # 100000 * 50 = ~5MB
    with mock.patch("ddtrace.internal.writer.log") as log:
        with t.trace("huge"):
            for i in range(100000):
                with tracer.trace("operation") as s:
                    s.set_tag("a" * 10, "b" * 40)
        t.shutdown()

        assert log.warning.call_count > 0
        calls = [mock.call("trace larger than payload limit (%s), dropping", 8000000)]
        log.warning.assert_has_calls(calls)


def test_trace_bad_url():
    t = Tracer()
    t.configure(hostname="bad", port=1111)

    with mock.patch("ddtrace.internal.writer.log") as log:
        with t.trace("op"):
            pass
        t.shutdown()

    assert log.error.call_count > 0
    calls = [mock.call("Failed to send traces to Datadog Agent at %s", "http://bad:1111", exc_info=True)]
    log.error.assert_has_calls(calls)


def test_writer_headers():
    t = Tracer()
    t.writer._put = mock.Mock(wraps=t.writer._put)
    with t.trace("op"):
        pass
    t.shutdown()
    assert t.writer._put.call_count == 1
    data, headers = t.writer._put.call_args[0]
    assert headers.get("Datadog-Meta-Tracer-Version") == ddtrace.__version__
    assert headers.get("Datadog-Meta-Lang") == "python"
    assert headers.get("Content-Type") == "application/msgpack"
    assert headers.get("X-Datadog-Trace-Count") == "1"

    t = Tracer()
    t.writer._put = mock.Mock(wraps=t.writer._put)
    for i in range(100):
        with t.trace("op"):
            pass
    t.shutdown()
    assert t.writer._put.call_count == 1
    data, headers = t.writer._put.call_args[0]
    assert headers.get("X-Datadog-Trace-Count") == "100"

import logging
import os
import subprocess
import sys

import mock
import pytest

import ddtrace
from ddtrace import Tracer
from ddtrace import tracer
from ddtrace.internal import agent
from ddtrace.internal.runtime import container
from ddtrace.internal.writer import AgentWriter
from ddtrace.vendor import six
from tests.utils import AnyFloat
from tests.utils import AnyInt
from tests.utils import AnyStr
from tests.utils import override_global_config


AGENT_VERSION = os.environ.get("AGENT_VERSION")


def test_configure_keeps_api_hostname_and_port():
    """
    Ensures that when calling configure without specifying hostname and port,
    previous overrides have been kept.
    """
    tracer = Tracer()
    if AGENT_VERSION == "testagent":
        assert tracer.writer.agent_url == "http://localhost:9126"
    else:
        assert tracer.writer.agent_url == "http://localhost:8126"
    tracer.configure(hostname="127.0.0.1", port=8127)
    assert tracer.writer.agent_url == "http://127.0.0.1:8127"
    tracer.configure(priority_sampling=True)
    assert tracer.writer.agent_url == "http://127.0.0.1:8127"


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


def test_output(tmpdir):
    f = tmpdir.join("test.py")
    f.write(
        """
import ddtrace
""".lstrip()
    )
    p = subprocess.Popen(
        ["ddtrace-run", sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
    )
    p.wait()
    assert p.stderr.read() == six.b("")
    assert p.stdout.read() == six.b("")
    assert p.returncode == 0


def test_start_in_thread(tmpdir):
    f = tmpdir.join("test.py")
    f.write(
        """
import threading

def target():
    import ddtrace

t = threading.Thread(target=target)
t.start()
t.join()
""".lstrip()
    )
    p = subprocess.Popen(
        ["ddtrace-run", sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
    )
    p.wait()
    assert p.stderr.read() == six.b("")
    assert p.stdout.read() == six.b("")
    assert p.returncode == 0


@pytest.mark.skipif(AGENT_VERSION != "latest", reason="Agent v5 doesn't support UDS")
def test_single_trace_uds():
    t = Tracer()
    sockdir = "/tmp/ddagent/trace.sock"
    t.configure(uds_path=sockdir)

    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("client.testing").finish()
        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


def test_uds_wrong_socket_path():
    t = Tracer()
    t.configure(uds_path="/tmp/ddagent/nosockethere")
    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("client.testing").finish()
        t.shutdown()
    calls = [
        mock.call("failed to send traces to Datadog Agent at %s", "unix:///tmp/ddagent/nosockethere", exc_info=True)
    ]
    log.error.assert_has_calls(calls)


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="FIXME: Test agent doesn't support this for some reason.")
def test_payload_too_large():
    t = Tracer()
    # Make sure a flush doesn't happen partway through.
    t.configure(writer=AgentWriter(agent.get_trace_url(), processing_interval=1000))
    with mock.patch("ddtrace.internal.writer.log") as log:
        for i in range(100000):
            with t.trace("operation") as s:
                s.set_tag(str(i), "b" * 190)
                s.set_tag(str(i), "a" * 190)

        t.shutdown()
        calls = [
            mock.call(
                "trace buffer (%s traces %db/%db) cannot fit trace of size %db, dropping",
                AnyInt(),
                AnyInt(),
                AnyInt(),
                AnyInt(),
            )
        ]
        log.warning.assert_has_calls(calls)
        log.error.assert_not_called()


def test_large_payload():
    t = Tracer()
    # Traces are approx. 275 bytes.
    # 10,000*275 ~ 3MB
    with mock.patch("ddtrace.internal.writer.log") as log:
        for i in range(10000):
            with t.trace("operation"):
                pass

        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


def test_child_spans():
    t = Tracer()
    with mock.patch("ddtrace.internal.writer.log") as log:
        spans = []
        for i in range(10000):
            spans.append(t.trace("op"))
        for s in spans:
            s.finish()

        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


def test_metrics():
    with override_global_config(dict(health_metrics_enabled=True)):
        t = Tracer()
        statsd_mock = mock.Mock()
        t.writer.dogstatsd = statsd_mock
        assert t.writer._report_metrics
        with mock.patch("ddtrace.internal.writer.log") as log:
            for _ in range(5):
                spans = []
                for i in range(3000):
                    spans.append(t.trace("op"))
                for s in spans:
                    s.finish()

            t.shutdown()
            log.warning.assert_not_called()
            log.error.assert_not_called()

        statsd_mock.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.buffer.accepted.traces", 5, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 15000, tags=[]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.sent.bytes", AnyInt()),
            ],
            any_order=True,
        )


def test_single_trace_too_large():
    t = Tracer()
    with mock.patch("ddtrace.internal.writer.log") as log:
        with t.trace("huge"):
            for i in range(100000):
                with tracer.trace("operation") as s:
                    s.set_tag("a" * 10, "b" * 10)
        t.shutdown()

        calls = [mock.call("trace (%db) larger than payload limit (%db), dropping", AnyInt(), AnyInt())]
        log.warning.assert_has_calls(calls)
        log.error.assert_not_called()


def test_trace_bad_url():
    t = Tracer()
    t.configure(hostname="bad", port=1111)

    with mock.patch("ddtrace.internal.writer.log") as log:
        with t.trace("op"):
            pass
        t.shutdown()

    calls = [mock.call("failed to send traces to Datadog Agent at %s", "http://bad:1111", exc_info=True)]
    log.error.assert_has_calls(calls)


def test_writer_headers():
    t = Tracer()
    t.writer._put = mock.Mock(wraps=t.writer._put)
    with t.trace("op"):
        pass
    t.shutdown()
    assert t.writer._put.call_count == 1
    _, headers = t.writer._put.call_args[0]
    assert headers.get("Datadog-Meta-Tracer-Version") == ddtrace.__version__
    assert headers.get("Datadog-Meta-Lang") == "python"
    assert headers.get("Content-Type") == "application/msgpack"
    assert headers.get("X-Datadog-Trace-Count") == "1"
    if container.get_container_info():
        assert "Datadog-Container-Id" in headers

    t = Tracer()
    t.writer._put = mock.Mock(wraps=t.writer._put)
    for _ in range(100):
        with t.trace("op"):
            pass
    t.shutdown()
    assert t.writer._put.call_count == 1
    _, headers = t.writer._put.call_args[0]
    assert headers.get("X-Datadog-Trace-Count") == "100"

    t = Tracer()
    t.writer._put = mock.Mock(wraps=t.writer._put)
    for _ in range(10):
        with t.trace("op"):
            for _ in range(5):
                t.trace("child").finish()
    t.shutdown()
    assert t.writer._put.call_count == 1
    _, headers = t.writer._put.call_args[0]
    assert headers.get("X-Datadog-Trace-Count") == "10"


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support priority sampling responses.")
def test_priority_sampling_response():
    # Send the data once because the agent doesn't respond with them on the
    # first payload.
    t = Tracer()
    s = t.trace("operation", service="my-svc")
    s.set_tag("env", "my-env")
    s.finish()
    assert "service:my-svc,env:my-env" not in t.writer._priority_sampler._by_service_samplers
    t.shutdown()

    # For some reason the agent doesn't start returning the service information
    # immediately
    import time

    time.sleep(5)

    t = Tracer()
    s = t.trace("operation", service="my-svc")
    s.set_tag("env", "my-env")
    s.finish()
    assert "service:my-svc,env:my-env" not in t.writer._priority_sampler._by_service_samplers
    t.shutdown()
    assert "service:my-svc,env:my-env" in t.writer._priority_sampler._by_service_samplers


def test_bad_endpoint():
    t = Tracer()
    t.writer._endpoint = "/bad"
    with mock.patch("ddtrace.internal.writer.log") as log:
        s = t.trace("operation", service="my-svc")
        s.set_tag("env", "my-env")
        s.finish()
        t.shutdown()
    calls = [
        mock.call(
            "unsupported endpoint '%s': received response %s from Datadog Agent (%s)",
            "/bad",
            404,
            t.writer.agent_url,
        )
    ]
    log.error.assert_has_calls(calls)


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="FIXME: Test agent response is different.")
def test_bad_payload():
    t = Tracer()

    class BadEncoder:
        def encode_trace(self, spans):
            return []

        def join_encoded(self, traces):
            return "not msgpack"

    t.writer._encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [
        mock.call(
            "failed to send traces to Datadog Agent at %s: HTTP error status %s, reason %s",
            "http://localhost:8126",
            400,
            "Bad Request",
        )
    ]
    log.error.assert_has_calls(calls)


def test_bad_encoder():
    t = Tracer()

    class BadEncoder:
        def encode_trace(self, spans):
            raise Exception()

        def join_encoded(self, traces):
            pass

    t.writer._encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [mock.call("failed to encode trace with encoder %r", t.writer._encoder, exc_info=True)]
    log.error.assert_has_calls(calls)


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support v0.3")
def test_downgrade():
    t = Tracer()
    t.writer._downgrade(None, None)
    assert t.writer._endpoint == "/v0.3/traces"
    with mock.patch("ddtrace.internal.writer.log") as log:
        s = t.trace("operation", service="my-svc")
        s.finish()
        t.shutdown()
    log.warning.assert_not_called()
    log.error.assert_not_called()


def test_span_tags():
    t = Tracer()
    with mock.patch("ddtrace.internal.writer.log") as log:
        s = t.trace("operation", service="my-svc")
        s.set_tag("env", "my-env")
        s.set_metric("number", 123)
        s.set_metric("number", 12.0)
        s.set_metric("number", "1")
        s.finish()
        t.shutdown()
    log.warning.assert_not_called()
    log.error.assert_not_called()


def test_synchronous_writer_shutdown():
    tracer = Tracer()
    tracer.configure(writer=AgentWriter(tracer.writer.agent_url, sync_mode=True))
    # Ensure this doesn't raise.
    tracer.shutdown()


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support empty trace payloads.")
def test_flush_log(caplog):
    caplog.set_level(logging.INFO)

    writer = AgentWriter(agent.get_trace_url())

    with mock.patch("ddtrace.internal.writer.log") as log:
        writer.write([])
        writer.flush_queue(raise_exc=True)
        calls = [
            mock.call(
                logging.DEBUG,
                "sent %s in %.5fs to %s",
                AnyStr(),
                AnyFloat(),
                writer.agent_url,
            )
        ]
        log.log.assert_has_calls(calls)

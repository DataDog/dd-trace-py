import mock
import os
import subprocess
import sys

import pytest

import ddtrace
from ddtrace import Tracer, tracer
from ddtrace.internal.writer import AgentWriter
from ddtrace.internal.runtime import container


if os.environ.get("AGENT_VERSION") == "5":
    AGENT_VERSION = 5
else:
    AGENT_VERSION = 7


class AnyStr(object):
    def __eq__(self, other):
        return isinstance(other, str)


class AnyInt(object):
    def __eq__(self, other):
        return isinstance(other, int)


def test_configure_keeps_api_hostname_and_port():
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


@pytest.mark.skipif(AGENT_VERSION < 6, reason="Agent v5 doesn't support UDS")
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


def test_payload_too_large():
    t = Tracer()
    # Make sure a flush doesn't happen partway through.
    t.configure(writer=AgentWriter(processing_interval=1000))
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
        # log.error.assert_not_called()


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
    calls = [mock.call("unsupported endpoint '%s': received response %s from Datadog Agent", "/bad", 404)]
    log.error.assert_has_calls(calls)


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

# -*- coding: utf-8 -*-
import itertools
import logging
import os
import subprocess
import sys
import time

import mock
import pytest
import six

import ddtrace
from ddtrace import Tracer
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.internal import agent
from ddtrace.internal import compat
from ddtrace.internal.ci_visibility.constants import AGENTLESS_ENDPOINT
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_ENDPOINT
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_EVENT_VALUE
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.ci_visibility.recorder import CIVisibility
from ddtrace.internal.ci_visibility.writer import CIVisibilityWriter
from ddtrace.internal.encoding import JSONEncoder
from ddtrace.internal.encoding import MsgpackEncoderV03 as Encoder
from ddtrace.internal.runtime import container
from ddtrace.internal.utils.http import Response
from ddtrace.internal.writer import AgentWriter
from tests.utils import AnyFloat
from tests.utils import AnyInt
from tests.utils import AnyStr
from tests.utils import call_program
from tests.utils import override_env
from tests.utils import override_global_config


AGENT_VERSION = os.environ.get("AGENT_VERSION")


def allencodings(f):
    return pytest.mark.parametrize("encoding", ["v0.5", "v0.4"])(f)


def test_configure_keeps_api_hostname_and_port():
    """
    Ensures that when calling configure without specifying hostname and port,
    previous overrides have been kept.
    """
    tracer = Tracer()
    if AGENT_VERSION == "testagent":
        assert tracer._writer.agent_url == "http://localhost:9126"
    else:
        assert tracer._writer.agent_url == "http://localhost:8126"
    tracer.configure(hostname="127.0.0.1", port=8127)
    assert tracer._writer.agent_url == "http://127.0.0.1:8127"
    tracer.configure(priority_sampling=True)
    assert tracer._writer.agent_url == "http://127.0.0.1:8127"


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

    env = os.environ.copy()
    env.update({"DD_TRACE_DEBUG": "true", "DD_CALL_BASIC_CONFIG": "true"})

    p = subprocess.Popen(
        [sys.executable, "-c", "import ddtrace"],
        env=env,
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


@allencodings
@pytest.mark.skipif(AGENT_VERSION != "latest", reason="Agent v5 doesn't support UDS")
def test_single_trace_uds(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    sockdir = "/tmp/ddagent/trace.sock"
    t.configure(uds_path=sockdir)

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("client.testing").finish()
        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


@allencodings
def test_uds_wrong_socket_path(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t.configure(uds_path="/tmp/ddagent/nosockethere")
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("client.testing").finish()
        t.shutdown()
    calls = [
        mock.call(
            "failed to send, dropping %d traces to intake at %s after %d retries",
            1,
            "unix:///tmp/ddagent/nosockethere/{}/traces".format(encoding if encoding else "v0.5"),
            3,
        )
    ]
    log.error.assert_has_calls(calls)


@allencodings
@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="FIXME: Test agent doesn't support this for some reason.")
def test_payload_too_large(encoding, monkeypatch):
    SIZE = 1 << 12  # 4KB
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)
    monkeypatch.setenv("DD_TRACE_WRITER_BUFFER_SIZE_BYTES", str(SIZE))
    monkeypatch.setenv("DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES", str(SIZE))

    t = Tracer()
    assert t._writer._max_payload_size == SIZE
    assert t._writer._buffer_size == SIZE
    # Make sure a flush doesn't happen partway through.
    t.configure(writer=AgentWriter(agent.get_trace_url(), processing_interval=1000))
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        for i in range(100000 if encoding == "v0.5" else 1000):
            with t.trace("operation") as s:
                s.set_tag(str(i), "b" * 190)
                s.set_tag(str(i), "a" * 190)

        t.shutdown()
        calls = [
            mock.call(
                "trace buffer (%s traces %db/%db) cannot fit trace of size %db, dropping (writer status: %s)",
                AnyInt(),
                AnyInt(),
                AnyInt(),
                AnyInt(),
                AnyStr(),
            )
        ]
        log.warning.assert_has_calls(calls)
        log.error.assert_not_called()


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="FIXME: Test agent doesn't support this for some reason.")
def test_resource_name_too_large(monkeypatch):
    SIZE = 1 << 12  # 4KB
    monkeypatch.setenv("DD_TRACE_API_VERSION", "v0.5")
    monkeypatch.setenv("DD_TRACE_WRITER_BUFFER_SIZE_BYTES", str(SIZE))

    t = Tracer()
    assert t._writer._buffer_size == SIZE
    s = t.trace("operation", service="foo")
    s.resource = "B" * (SIZE + 1)
    try:
        s.finish()
    except ValueError:
        pytest.fail()
    encoded_spans = t._writer._encoder.encode()
    assert b"<dropped string of length 4097 because it's too long (max allowed length 4096)>" in encoded_spans


@allencodings
def test_large_payload(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    # Traces are approx. 275 bytes.
    # 10,000*275 ~ 3MB
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        for i in range(10000):
            with t.trace("operation"):
                pass

        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


@allencodings
def test_child_spans(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        spans = []
        for i in range(10000):
            spans.append(t.trace("op"))
        for s in spans:
            s.finish()

        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


@allencodings
def test_metrics(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    with override_global_config(dict(health_metrics_enabled=True)):
        t = Tracer()
        assert t._partial_flush_min_spans == 500
        statsd_mock = mock.Mock()
        t._writer.dogstatsd = statsd_mock
        with mock.patch("ddtrace.internal.writer.writer.log") as log:
            for _ in range(5):
                spans = []
                for i in range(3000):
                    spans.append(t.trace("op"))
                # Since _partial_flush_min_spans is set to 500 we will flush spans in 6 batches
                # each batch will contain 500 spans
                for s in spans:
                    s.finish()

            t.shutdown()
            log.warning.assert_not_called()
            log.error.assert_not_called()

        statsd_mock.distribution.assert_has_calls(
            [
                mock.call("datadog.tracer.http.sent.bytes", AnyInt()),
                mock.call("datadog.tracer.http.sent.traces", 30),
                mock.call("datadog.tracer.writer.accepted.traces", 30, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.traces", 30, tags=[]),
                mock.call("datadog.tracer.buffer.accepted.spans", 15000, tags=[]),
                mock.call("datadog.tracer.http.requests", 1, tags=[]),
                mock.call("datadog.tracer.http.sent.bytes", AnyInt(), tags=[]),
            ],
            any_order=True,
        )


@allencodings
def test_metrics_partial_flush_disabled(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    with override_global_config(dict(health_metrics_enabled=True)):
        t = Tracer()
        t.configure(
            partial_flush_enabled=False,
        )
        statsd_mock = mock.Mock()
        t._writer.dogstatsd = statsd_mock
        with mock.patch("ddtrace.internal.writer.writer.log") as log:
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


@allencodings
def test_single_trace_too_large(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    assert t._partial_flush_enabled is True
    # This test asserts that a BufferFull exception is raised. We need to ensure the encoders queue is not flushed
    # while trace chunks are being queued.
    with mock.patch.object(AgentWriter, "flush_queue", return_value=None):
        with mock.patch("ddtrace.internal.writer.writer.log") as log:
            key = "a" * 250
            with t.trace("huge"):
                for i in range(30000):
                    with t.trace("operation") as s:
                        # Need to make the strings unique so that the v0.5 encoding doesn’t compress the data
                        s.set_tag(key + str(i), key + str(i))
            assert (
                mock.call(
                    "trace buffer (%s traces %db/%db) cannot fit trace of size %db, dropping (writer status: %s)",
                    AnyInt(),
                    AnyInt(),
                    AnyInt(),
                    AnyInt(),
                    AnyStr(),
                )
                in log.warning.mock_calls
            ), log.mock_calls[
                :20
            ]  # limits number of logs, this test could generate hundreds of thousands of logs.
            log.error.assert_not_called()


@allencodings
def test_single_trace_too_large_partial_flush_disabled(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t.configure(
        partial_flush_enabled=False,
    )
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        with t.trace("huge"):
            for i in range(200000):
                with t.trace("operation") as s:
                    s.set_tag("a" * 10, "b" * 10)
        t.shutdown()

        calls = [mock.call("trace (%db) larger than payload buffer item limit (%db), dropping", AnyInt(), AnyInt())]
        log.warning.assert_has_calls(calls)
        log.error.assert_not_called()


@allencodings
def test_trace_bad_url(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t.configure(hostname="bad", port=1111)

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        with t.trace("op"):
            pass
        t.shutdown()

    calls = [
        mock.call(
            "failed to send, dropping %d traces to intake at %s after %d retries",
            1,
            "http://bad:1111/{}/traces".format(encoding if encoding else "v0.5"),
            3,
        )
    ]
    log.error.assert_has_calls(calls)


@allencodings
def test_writer_headers(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t._writer._put = mock.Mock(wraps=t._writer._put)
    with t.trace("op"):
        pass
    t.shutdown()
    assert t._writer._put.call_count == 1
    _, headers, _ = t._writer._put.call_args[0]
    assert headers.get("Datadog-Meta-Tracer-Version") == ddtrace.__version__
    assert headers.get("Datadog-Meta-Lang") == "python"
    assert headers.get("Content-Type") == "application/msgpack"
    assert headers.get("X-Datadog-Trace-Count") == "1"
    if container.get_container_info():
        assert "Datadog-Container-Id" in headers

    t = Tracer()
    t._writer._put = mock.Mock(wraps=t._writer._put)
    for _ in range(100):
        with t.trace("op"):
            pass
    t.shutdown()
    assert t._writer._put.call_count == 1
    _, headers, _ = t._writer._put.call_args[0]
    assert headers.get("X-Datadog-Trace-Count") == "100"

    t = Tracer()
    t._writer._put = mock.Mock(wraps=t._writer._put)
    for _ in range(10):
        with t.trace("op"):
            for _ in range(5):
                t.trace("child").finish()
    t.shutdown()
    assert t._writer._put.call_count == 1
    _, headers, _ = t._writer._put.call_args[0]
    assert headers.get("X-Datadog-Trace-Count") == "10"


def _prime_tracer_with_priority_sample_rate_from_agent(t, service, env):
    # Send the data once because the agent doesn't respond with them on the
    # first payload.
    s = t.trace("operation", service=service)
    s.finish()
    t.flush()

    sampler_key = "service:{},env:{}".format(service, env)
    while sampler_key not in t._writer._priority_sampler._by_service_samplers:
        time.sleep(1)
        s = t.trace("operation", service=service)
        s.finish()
        t.flush()


def _turn_tracer_into_dummy(tracer):
    """Override tracer's writer's write() method to keep traces instead of sending them away"""

    def monkeypatched_write(self, spans=None):
        if spans:
            traces = [spans]
            self.json_encoder.encode_traces(traces)
            self.msgpack_encoder.put(spans)
            self.msgpack_encoder.encode()
            self.spans += spans
            self.traces += traces

    tracer._writer.spans = []
    tracer._writer.traces = []
    tracer._writer.json_encoder = JSONEncoder()
    tracer._writer.msgpack_encoder = Encoder(4 << 20, 4 << 20)
    tracer._writer.write = monkeypatched_write.__get__(tracer._writer, AgentWriter)


@allencodings
@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support priority sampling responses.")
def test_priority_sampling_response(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    _id = time.time()
    env = "my-env-{}".format(_id)
    with override_global_config(dict(env=env)):
        service = "my-svc-{}".format(_id)
        sampler_key = "service:{},env:{}".format(service, env)
        t = Tracer()
        assert sampler_key not in t._writer._priority_sampler._by_service_samplers
        _prime_tracer_with_priority_sample_rate_from_agent(t, service, env)
        assert sampler_key in t._writer._priority_sampler._by_service_samplers
        t.shutdown()


@allencodings
@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support priority sampling responses.")
def test_priority_sampling_rate_honored(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    _id = time.time()
    env = "my-env-{}".format(_id)
    with override_global_config(dict(env=env)):
        service = "my-svc-{}".format(_id)
        t = Tracer()

        # send a ton of traces from different services to make the agent adjust its sample rate for ``service,env``
        for i in range(100):
            s = t.trace("operation", service="dummysvc{}".format(i))
            s.finish()
        t.flush()

        _prime_tracer_with_priority_sample_rate_from_agent(t, service, env)
        sampler_key = "service:{},env:{}".format(service, env)
        assert sampler_key in t._writer._priority_sampler._by_service_samplers

        rate_from_agent = t._writer._priority_sampler._by_service_samplers[sampler_key].sample_rate
        assert 0 < rate_from_agent < 1

        _turn_tracer_into_dummy(t)
        captured_span_count = 100
        for _ in range(captured_span_count):
            with t.trace("operation", service=service) as s:
                pass
            t.flush()
        assert len(t._writer.traces) == captured_span_count
        sampled_spans = [s for s in t._writer.spans if s.context._metrics[SAMPLING_PRIORITY_KEY] == AUTO_KEEP]
        sampled_ratio = len(sampled_spans) / captured_span_count
        diff_magnitude = abs(sampled_ratio - rate_from_agent)
        assert (
            diff_magnitude < 0.3
        ), "the proportion of sampled spans should approximate the sample rate given by the agent"

        t.shutdown()


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support evp proxy.")
def test_civisibility_intake_with_evp_available():
    with override_env(dict(DD_API_KEY="foobar.baz", DD_SITE="foo.bar")):
        with override_global_config({"_ci_visibility_agentless_enabled": False}):
            t = Tracer()
            CIVisibility.enable(tracer=t)
            assert CIVisibility._instance.tracer._writer._endpoint == EVP_PROXY_AGENT_ENDPOINT
            assert CIVisibility._instance.tracer._writer.intake_url == agent.get_trace_url()
            assert (
                CIVisibility._instance.tracer._writer._headers[EVP_SUBDOMAIN_HEADER_NAME]
                == EVP_SUBDOMAIN_HEADER_EVENT_VALUE
            )
            CIVisibility.disable()


def test_civisibility_intake_with_missing_apikey():
    with override_env(dict(DD_SITE="foobar.baz")):
        with override_global_config({"_ci_visibility_agentless_enabled": True}):
            with pytest.raises(EnvironmentError):
                CIVisibility.enable()


def test_civisibility_intake_with_apikey():
    with override_env(dict(DD_API_KEY="foobar.baz", DD_SITE="foo.bar")):
        with override_global_config({"_ci_visibility_agentless_enabled": True}):
            t = Tracer()
            CIVisibility.enable(tracer=t)
            assert CIVisibility._instance.tracer._writer._endpoint == AGENTLESS_ENDPOINT
            assert CIVisibility._instance.tracer._writer.intake_url == "https://citestcycle-intake.foo.bar"
            CIVisibility.disable()


def test_bad_endpoint():
    t = Tracer()
    for client in t._writer._clients:
        client.ENDPOINT = "/bad"
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        s = t.trace("operation", service="my-svc")
        s.set_tag("env", "my-env")
        s.finish()
        t.shutdown()
    calls = [
        mock.call(
            "unsupported endpoint '%s': received response %s from intake (%s)",
            "/bad",
            404,
            t._writer.agent_url,
        )
    ]
    log.error.assert_has_calls(calls)


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="FIXME: Test agent response is different.")
def test_bad_payload():
    t = Tracer()

    class BadEncoder:
        content_type = ""

        def __len__(self):
            return 0

        def put(self, trace):
            pass

        def encode(self):
            return ""

        def encode_traces(self, traces):
            return ""

    for client in t._writer._clients:
        client.encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [
        mock.call(
            "failed to send traces to intake at %s: HTTP error status %s, reason %s",
            "http://localhost:8126/v0.5/traces",
            400,
            "Bad Request",
        )
    ]
    log.error.assert_has_calls(calls)


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="FIXME: Test agent response is different.")
def test_bad_payload_log_payload(monkeypatch):
    monkeypatch.setenv("_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS", "true")
    t = Tracer()

    class BadEncoder:
        content_type = ""

        def __len__(self):
            return 0

        def put(self, trace):
            pass

        def encode(self):
            return b"bad_payload"

        def encode_traces(self, traces):
            return b"bad_payload"

    for client in t._writer._clients:
        client.encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [
        mock.call(
            "failed to send traces to intake at %s: HTTP error status %s, reason %s, payload %s",
            "http://localhost:8126/v0.5/traces",
            400,
            "Bad Request",
            "6261645f7061796c6f6164",
        )
    ]
    log.error.assert_has_calls(calls)


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="FIXME: Test agent response is different.")
def test_bad_payload_log_payload_non_bytes(monkeypatch):
    monkeypatch.setenv("_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS", "true")
    t = Tracer()

    class BadEncoder:
        content_type = ""

        def __len__(self):
            return 0

        def put(self, trace):
            pass

        def encode(self):
            # Python 2.7
            # >>> isinstance("", bytes)
            # True
            # >>> isinstance(u"", bytes)
            # False
            # Python 3
            # >>> isinstance("", bytes)
            # False
            # >>> isinstance(u"", bytes)
            # False
            return u"bad_payload"

        def encode_traces(self, traces):
            return u"bad_payload"

    for client in t._writer._clients:
        client.encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [
        mock.call(
            "failed to send traces to intake at %s: HTTP error status %s, reason %s, payload %s",
            "http://localhost:8126/v0.5/traces",
            400,
            "Bad Request",
            "bad_payload",
        )
    ]
    log.error.assert_has_calls(calls)


def test_bad_encoder():
    t = Tracer()

    class BadEncoder:
        def __len__(self):
            return 0

        def put(self, trace):
            pass

        def encode(self):
            raise Exception()

        def encode_traces(self, traces):
            raise Exception()

    for client in t._writer._clients:
        client.encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [mock.call("failed to encode trace with encoder %r", t._writer._encoder, exc_info=True)]
    log.error.assert_has_calls(calls)


@allencodings
@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support v0.3")
def test_downgrade(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t._writer._downgrade(None, None, t._writer._clients[0])
    assert t._writer._endpoint == {"v0.5": "v0.4/traces", "v0.4": "v0.3/traces"}[encoding or "v0.5"]
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        s = t.trace("operation", service="my-svc")
        s.finish()
        t.shutdown()
    log.warning.assert_not_called()
    log.error.assert_not_called()


@allencodings
def test_span_tags(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
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
    tracer.configure(writer=AgentWriter(tracer._writer.agent_url, sync_mode=True))
    # Ensure this doesn't raise.
    tracer.shutdown()


@allencodings
@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support empty trace payloads.")
def test_flush_log(caplog, encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    caplog.set_level(logging.INFO)

    writer = AgentWriter(agent.get_trace_url())

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        writer.write([])
        writer.flush_queue(raise_exc=True)
        # for latest agent, default to v0.3 since no priority sampler is set
        expected_encoding = "v0.3" if AGENT_VERSION == "v5" else (encoding or "v0.3")
        calls = [
            mock.call(
                logging.DEBUG,
                "sent %s in %.5fs to %s",
                AnyStr(),
                AnyFloat(),
                "{}/{}/traces".format(writer.agent_url, expected_encoding),
            )
        ]
        log.log.assert_has_calls(calls)


@pytest.mark.parametrize("logs_injection,debug_mode,patch_logging", itertools.product([True, False], repeat=3))
def test_regression_logging_in_context(tmpdir, logs_injection, debug_mode, patch_logging):
    """
    When logs injection is enabled and the logger is patched
        When a parent span closes before a child
            The application does not deadlock due to context lock acquisition
    """
    f = tmpdir.join("test.py")
    f.write(
        """
import ddtrace
ddtrace.patch(logging=%s)

s1 = ddtrace.tracer.trace("1")
s2 = ddtrace.tracer.trace("2")
s1.finish()
s2.finish()
""".lstrip()
        % str(patch_logging)
    )

    env = os.environ.copy()
    env.update(
        {
            "DD_TRACE_LOGS_INJECTION": str(logs_injection).lower(),
            "DD_TRACE_DEBUG": str(debug_mode).lower(),
            "DD_CALL_BASIC_CONFIG": "true",
        }
    )

    p = subprocess.Popen(
        [sys.executable, "test.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(tmpdir), env=env
    )
    try:
        p.wait(timeout=25)
    except TypeError:
        # timeout argument added in Python 3.3
        p.wait()
    assert p.returncode == 0


@pytest.mark.parametrize(
    "call_basic_config,debug_mode",
    itertools.permutations((True, False, None), 2),
)
def test_call_basic_config(ddtrace_run_python_code_in_subprocess, call_basic_config, debug_mode):
    """
    When setting DD_CALL_BASIC_CONFIG env variable
        When true
            We call logging.basicConfig()
        When false
            We do not call logging.basicConfig()
        When not set
            We do not call logging.basicConfig()
    """
    env = os.environ.copy()

    if debug_mode is not None:
        env["DD_TRACE_DEBUG"] = str(debug_mode).lower()
    if call_basic_config is not None:
        env["DD_CALL_BASIC_CONFIG"] = str(call_basic_config).lower()
        has_root_handlers = call_basic_config
    else:
        has_root_handlers = False

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import logging
root = logging.getLogger()
print(len(root.handlers))
""",
        env=env,
    )

    assert status == 0
    if has_root_handlers:
        assert out == six.b("1\n")
    else:
        assert out == six.b("0\n")


@pytest.mark.subprocess(
    env=dict(
        DD_TRACE_WRITER_BUFFER_SIZE_BYTES="1000",
        DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES="5000",
        DD_TRACE_WRITER_INTERVAL_SECONDS="5.0",
    )
)
def test_writer_env_configuration():
    import ddtrace

    assert ddtrace.tracer._writer._encoder.max_size == 1000
    assert ddtrace.tracer._writer._encoder.max_item_size == 1000
    assert ddtrace.tracer._writer._interval == 5.0


@pytest.mark.subprocess
def test_writer_env_configuration_defaults():
    import ddtrace

    assert ddtrace.tracer._writer._encoder.max_size == 8 << 20
    assert ddtrace.tracer._writer._encoder.max_item_size == 8 << 20
    assert ddtrace.tracer._writer._interval == 1.0


def test_writer_env_configuration_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    env["DD_TRACE_WRITER_BUFFER_SIZE_BYTES"] = "1000"
    env["DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES"] = "5000"
    env["DD_TRACE_WRITER_INTERVAL_SECONDS"] = "5.0"

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace

assert ddtrace.tracer._writer._encoder.max_size == 1000
assert ddtrace.tracer._writer._encoder.max_item_size == 1000
assert ddtrace.tracer._writer._interval == 5.0
""",
        env=env,
    )
    assert status == 0, (out, err)


def test_writer_env_configuration_ddtrace_run_defaults(ddtrace_run_python_code_in_subprocess):
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace

assert ddtrace.tracer._writer._encoder.max_size == 8 << 20
assert ddtrace.tracer._writer._encoder.max_item_size == 8 << 20
assert ddtrace.tracer._writer._interval == 1.0
""",
    )
    assert status == 0, (out, err)


@allencodings
def test_partial_flush_log(run_python_code_in_subprocess, encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    partial_flush_min_spans = 2
    t = Tracer()

    t.configure(
        partial_flush_min_spans=partial_flush_min_spans,
    )

    s1 = t.trace("1")
    s2 = t.trace("2")
    s3 = t.trace("3")
    t_id = s3.trace_id

    with mock.patch("ddtrace.internal.processor.trace.log") as log:
        s3.finish()
        s2.finish()

    calls = [
        mock.call("trace %d has %d spans, %d finished", t_id, 3, 1),
        mock.call("Partially flushing %d spans for trace %d", partial_flush_min_spans, t_id),
    ]

    log.debug.assert_has_calls(calls)
    s1.finish()
    t.shutdown()


def test_ddtrace_run_startup_logging_injection(ddtrace_run_python_code_in_subprocess):
    """
    Regression test for enabling debug logging and logs injection

    When both DD_TRACE_DEBUG and DD_LOGS_INJECTION are enabled
    any logging during tracer initialization would raise an exception
    because `dd.service` was not available in the log record yet.
    """
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_LOGS_INJECTION"] = "true"
    env["DD_CALL_BASIC_CONFIG"] = "true"

    # DEV: We don't actually have to execute any code to validate this
    out, err, status, pid = ddtrace_run_python_code_in_subprocess("", env=env)

    # The program will always exit successfully
    # Errors during logging do not crash the app
    assert status == 0, (out, err)

    # The program does nothing
    assert out == b""

    # stderr is expected to log something due to debug logging
    assert b"[dd.service= dd.env= dd.version= dd.trace_id=0 dd.span_id=0]" in err

    # Assert no logging exceptions in stderr
    assert b"KeyError: 'dd.service'" not in err
    assert b"ValueError: Formatting field not found in record: 'dd.service'" not in err


def test_no_warnings():
    env = os.environ.copy()
    # Have to disable sqlite3 as coverage uses it on process shutdown
    # which results in a trace being generated after the tracer shutdown
    # has been initiated which results in a deprecation warning.
    env["DD_TRACE_SQLITE3_ENABLED"] = "false"
    out, err, _, _ = call_program("ddtrace-run", sys.executable, "-Wall", "-c", "'import ddtrace'", env=env)
    assert out == b"", out
    assert err == b"", err


def test_civisibility_event_endpoints():
    with override_env(dict(DD_API_KEY="foobar.baz")):
        with override_global_config({"_ci_visibility_code_coverage_enabled": True}):
            t = Tracer()
            t.configure(writer=CIVisibilityWriter(reuse_connections=True))
            t._writer._conn = mock.MagicMock()
            with mock.patch("ddtrace.internal.writer.Response.from_http_response") as from_http_response:
                from_http_response.return_value.__class__ = Response
                from_http_response.return_value.status = 200
                s = t.trace("operation", service="svc-no-cov")
                s.finish()
                span = t.trace("operation2", service="my-svc2")
                span.set_tag(
                    COVERAGE_TAG_NAME,
                    '{"files": [{"filename": "test_cov.py", "segments": [[5, 0, 5, 0, -1]]}, '
                    + '{"filename": "test_module.py", "segments": [[2, 0, 2, 0, -1]]}]}',
                )
                span.finish()
                conn = t._writer._conn
                t.shutdown()
            assert conn.request.call_count == (2 if compat.PY3 else 1)
            assert conn.request.call_args_list[0].args[1] == "api/v2/citestcycle"
            assert (
                b"svc-no-cov" in conn.request.call_args_list[0].args[2]
            ), "requests to the cycle endpoint should include non-coverage spans"
            if compat.PY3:
                assert conn.request.call_args_list[1].args[1] == "api/v2/citestcov"
                assert (
                    b"svc-no-cov" not in conn.request.call_args_list[1].args[2]
                ), "requests to the coverage endpoint should not include non-coverage spans"

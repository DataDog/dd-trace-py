import itertools
import logging
import os
import subprocess
import sys

import mock
import pytest
import six

import ddtrace
from ddtrace import Tracer
from ddtrace.internal import agent
from ddtrace.internal.runtime import container
from ddtrace.internal.writer import AgentWriter
from tests.utils import AnyFloat
from tests.utils import AnyInt
from tests.utils import AnyStr
from tests.utils import call_program
from tests.utils import override_global_config


AGENT_VERSION = os.environ.get("AGENT_VERSION")


def allencodings(f):
    return pytest.mark.parametrize("encoding", ["", "v0.5"] if AGENT_VERSION != "v5" else [""])(f)


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


@allencodings
@pytest.mark.skipif(AGENT_VERSION != "latest", reason="Agent v5 doesn't support UDS")
def test_single_trace_uds(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    sockdir = "/tmp/ddagent/trace.sock"
    t.configure(uds_path=sockdir)

    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("client.testing").finish()
        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


@allencodings
def test_uds_wrong_socket_path(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t.configure(uds_path="/tmp/ddagent/nosockethere")
    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("client.testing").finish()
        t.shutdown()
    calls = [
        mock.call(
            "failed to send traces to Datadog Agent at %s",
            "unix:///tmp/ddagent/nosockethere/{}/traces".format(encoding if encoding else "v0.4"),
            exc_info=True,
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
    assert t.writer._max_payload_size == SIZE
    assert t.writer._buffer_size == SIZE
    # Make sure a flush doesn't happen partway through.
    t.configure(writer=AgentWriter(agent.get_trace_url(), processing_interval=1000))
    with mock.patch("ddtrace.internal.writer.log") as log:
        for i in range(100000 if encoding == "v0.5" else 1000):
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


@allencodings
def test_large_payload(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

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


@allencodings
def test_child_spans(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

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


@allencodings
def test_metrics(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

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


@allencodings
def test_single_trace_too_large(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    with mock.patch("ddtrace.internal.writer.log") as log:
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

    with mock.patch("ddtrace.internal.writer.log") as log:
        with t.trace("op"):
            pass
        t.shutdown()

    calls = [
        mock.call(
            "failed to send traces to Datadog Agent at %s",
            "http://bad:1111/{}/traces".format(encoding if encoding else "v0.4"),
            exc_info=True,
        )
    ]
    log.error.assert_has_calls(calls)


@allencodings
def test_writer_headers(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

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


@allencodings
@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support priority sampling responses.")
def test_priority_sampling_response(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

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
        def __len__(self):
            return 0

        def put(self, trace):
            pass

        def encode(self):
            return ""

        def encode_traces(self, traces):
            return ""

    t.writer._encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [
        mock.call(
            "failed to send traces to Datadog Agent at %s: HTTP error status %s, reason %s",
            "http://localhost:8126/v0.4/traces",
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
        def __len__(self):
            return 0

        def put(self, trace):
            pass

        def encode(self):
            return b"bad_payload"

        def encode_traces(self, traces):
            return b"bad_payload"

    t.writer._encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [
        mock.call(
            "failed to send traces to Datadog Agent at %s: HTTP error status %s, reason %s, payload %s",
            "http://localhost:8126/v0.4/traces",
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

    t.writer._encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [
        mock.call(
            "failed to send traces to Datadog Agent at %s: HTTP error status %s, reason %s, payload %s",
            "http://localhost:8126/v0.4/traces",
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

    t.writer._encoder = BadEncoder()
    with mock.patch("ddtrace.internal.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    calls = [mock.call("failed to encode trace with encoder %r", t.writer._encoder, exc_info=True)]
    log.error.assert_has_calls(calls)


@allencodings
@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support v0.3")
def test_downgrade(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t.writer._downgrade(None, None)
    assert t.writer._endpoint == {"v0.5": "v0.4/traces", "v0.4": "v0.3/traces"}[encoding or "v0.4"]
    with mock.patch("ddtrace.internal.writer.log") as log:
        s = t.trace("operation", service="my-svc")
        s.finish()
        t.shutdown()
    log.warning.assert_not_called()
    log.error.assert_not_called()


@allencodings
def test_span_tags(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

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


@allencodings
@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support empty trace payloads.")
def test_flush_log(caplog, encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    caplog.set_level(logging.INFO)

    writer = AgentWriter(agent.get_trace_url())

    with mock.patch("ddtrace.internal.writer.log") as log:
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
    p = subprocess.Popen(
        [sys.executable, "test.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmpdir),
        env=dict(
            DD_TRACE_LOGS_INJECTION=str(logs_injection).lower(),
            DD_TRACE_DEBUG=str(debug_mode).lower(),
        ),
    )
    try:
        p.wait(timeout=2)
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
            We call logging.basicConfig()
    """
    env = os.environ.copy()

    if debug_mode is not None:
        env["DD_TRACE_DEBUG"] = str(debug_mode).lower()
    if call_basic_config is not None:
        env["DD_CALL_BASIC_CONFIG"] = str(call_basic_config).lower()
        has_root_handlers = call_basic_config
    else:
        has_root_handlers = True

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


def test_writer_env_configuration(run_python_code_in_subprocess):
    env = os.environ.copy()
    env["DD_TRACE_WRITER_BUFFER_SIZE_BYTES"] = "1000"
    env["DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES"] = "5000"
    env["DD_TRACE_WRITER_INTERVAL_SECONDS"] = "5.0"

    out, err, status, pid = run_python_code_in_subprocess(
        """
import ddtrace

assert ddtrace.tracer.writer._encoder.max_size == 1000
assert ddtrace.tracer.writer._encoder.max_item_size == 1000
assert ddtrace.tracer.writer._interval == 5.0
""",
        env=env,
    )
    assert status == 0, (out, err)


def test_writer_env_configuration_defaults(run_python_code_in_subprocess):
    out, err, status, pid = run_python_code_in_subprocess(
        """
import ddtrace

assert ddtrace.tracer.writer._encoder.max_size == 8 << 20
assert ddtrace.tracer.writer._encoder.max_item_size == 8 << 20
assert ddtrace.tracer.writer._interval == 1.0
""",
    )
    assert status == 0, (out, err)


def test_writer_env_configuration_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    env["DD_TRACE_WRITER_BUFFER_SIZE_BYTES"] = "1000"
    env["DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES"] = "5000"
    env["DD_TRACE_WRITER_INTERVAL_SECONDS"] = "5.0"

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace

assert ddtrace.tracer.writer._encoder.max_size == 1000
assert ddtrace.tracer.writer._encoder.max_item_size == 1000
assert ddtrace.tracer.writer._interval == 5.0
""",
        env=env,
    )
    assert status == 0, (out, err)


def test_writer_env_configuration_ddtrace_run_defaults(ddtrace_run_python_code_in_subprocess):
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace

assert ddtrace.tracer.writer._encoder.max_size == 8 << 20
assert ddtrace.tracer.writer._encoder.max_item_size == 8 << 20
assert ddtrace.tracer.writer._interval == 1.0
""",
    )
    assert status == 0, (out, err)


@allencodings
def test_partial_flush_log(run_python_code_in_subprocess, encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    partial_flush_min_spans = 2
    t = Tracer()

    t.configure(
        partial_flush_enabled=True,
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
    out, err, status, pid = call_program("ddtrace-run", sys.executable, "-Wall", "-c", "'import ddtrace'", env=env)
    assert out == b"", out

    # Wrapt is using features deprecated in Python 3.10
    # See https://github.com/GrahamDumpleton/wrapt/issues/200
    if sys.version_info < (3, 10, 0):
        assert err == b"", err
    else:
        assert (
            err
            == (
                b"<frozen importlib._bootstrap>:914: ImportWarning: "
                b"ImportHookFinder.find_spec() not found; falling back to find_module()\n"
            )
            * 75
        ), err

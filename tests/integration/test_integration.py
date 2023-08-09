# -*- coding: utf-8 -*-
import itertools
import logging
import os
import sys

import mock
import pytest
import six

import ddtrace
from ddtrace import Tracer
from ddtrace.internal import agent
from ddtrace.internal.runtime import container
from ddtrace.internal.writer import AgentWriter
from tests.integration.utils import AGENT_VERSION
from tests.integration.utils import BadEncoder
from tests.integration.utils import import_ddtrace_in_subprocess
from tests.integration.utils import parametrize_with_all_encodings
from tests.integration.utils import send_invalid_payload_and_get_logs
from tests.integration.utils import skip_if_testagent
from tests.utils import AnyFloat
from tests.utils import AnyInt
from tests.utils import AnyStr
from tests.utils import call_program
from tests.utils import override_global_config


FOUR_KB = 1 << 12
LONG_STRING = "a" * 250


def test_configure_keeps_api_hostname_and_port():
    tracer = Tracer()
    assert tracer._writer.agent_url == "http://localhost:{}".format("9126" if AGENT_VERSION == "testagent" else "8126")
    tracer.configure(hostname="127.0.0.1", port=8127)
    assert tracer._writer.agent_url == "http://127.0.0.1:8127"
    tracer.configure(priority_sampling=True)
    assert (
        tracer._writer.agent_url == "http://127.0.0.1:8127"
    ), "Previous overrides of hostname and port are retained after a configure() call without those arguments"


def test_debug_mode_generates_debug_output():
    p = import_ddtrace_in_subprocess(None)
    assert p.stdout.read() == b""
    assert b"DEBUG:ddtrace" not in p.stderr.read(), "stderr should have no debug lines when DD_TRACE_DEBUG is unset"

    env = os.environ.copy()
    env.update({"DD_TRACE_DEBUG": "true", "DD_CALL_BASIC_CONFIG": "true"})
    p = import_ddtrace_in_subprocess(env)
    assert p.stdout.read() == b""
    assert b"DEBUG:ddtrace" in p.stderr.read(), "stderr should have some debug lines when DD_TRACE_DEBUG is set"


def test_import_ddtrace_generates_no_output_by_default(ddtrace_run_python_code_in_subprocess):
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace
""".lstrip()
    )
    assert err == six.b("")
    assert out == six.b("")
    assert status == 0


def test_start_in_thread_generates_no_output(ddtrace_run_python_code_in_subprocess):
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import threading

def target():
    import ddtrace

t = threading.Thread(target=target)
t.start()
t.join()
""".lstrip()
    )
    assert err == six.b("")
    assert out == six.b("")
    assert status == 0


@parametrize_with_all_encodings
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


@parametrize_with_all_encodings
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


@parametrize_with_all_encodings
@skip_if_testagent
def test_payload_too_large(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)
    monkeypatch.setenv("DD_TRACE_WRITER_BUFFER_SIZE_BYTES", str(FOUR_KB))
    monkeypatch.setenv("DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES", str(FOUR_KB))

    t = Tracer()
    assert t._writer._max_payload_size == FOUR_KB
    assert t._writer._buffer_size == FOUR_KB
    # use a long processing_interval to ensure a flush doesn't happen partway through
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


@skip_if_testagent
def test_resource_name_too_large(monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", "v0.5")
    monkeypatch.setenv("DD_TRACE_WRITER_BUFFER_SIZE_BYTES", str(FOUR_KB))

    t = Tracer()
    assert t._writer._buffer_size == FOUR_KB
    s = t.trace("operation", service="foo")
    # Maximum string length is set to 10% of the maximum buffer size
    s.resource = "B" * int(0.1*FOUR_KB + 1)
    try:
        s.finish()
    except ValueError:
        pytest.fail()
    encoded_spans = t._writer._encoder.encode()
    assert b"<dropped string of length 410 because it's too long (max allowed length 409)>" in encoded_spans


@parametrize_with_all_encodings
def test_large_payload_is_sent_without_warning_logs(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        for i in range(10000):
            with t.trace("operation"):
                pass

        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


@parametrize_with_all_encodings
def test_child_spans_do_not_cause_warning_logs(encoding, monkeypatch):
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


def _test_metrics(
    tracer,
    http_sent_traces=-1,
    writer_accepted_traces=-1,
    buffer_accepted_traces=-1,
    buffer_accepted_spans=-1,
    http_requests=-1,
    http_sent_bytes=-1,
):
    with override_global_config(dict(health_metrics_enabled=True)):
        statsd_mock = mock.Mock()
        tracer._writer.dogstatsd = statsd_mock
        with mock.patch("ddtrace.internal.writer.writer.log") as log:
            for _ in range(5):
                spans = []
                for i in range(3000):
                    spans.append(tracer.trace("op"))
                for s in spans:
                    s.finish()

            tracer.shutdown()
            log.warning.assert_not_called()
            log.error.assert_not_called()

        for metric_name, metric_value, check_tags in (
            ("datadog.tracer.http.sent.traces", http_sent_traces, False),
            ("datadog.tracer.writer.accepted.traces", writer_accepted_traces, True),
            ("datadog.tracer.buffer.accepted.traces", buffer_accepted_traces, True),
            ("datadog.tracer.buffer.accepted.spans", buffer_accepted_spans, True),
            ("datadog.tracer.http.requests", http_requests, True),
            ("datadog.tracer.http.sent.bytes", http_sent_bytes, True),
        ):
            if metric_value != -1:
                kwargs = {"tags": []} if check_tags else {}
                statsd_mock.distribution.assert_has_calls(
                    [mock.call(metric_name, metric_value, **kwargs)], any_order=True
                )


@parametrize_with_all_encodings
def test_metrics(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    with override_global_config(dict(health_metrics_enabled=True)):
        t = Tracer()
        assert t._partial_flush_min_spans == 500
        _test_metrics(
            t,
            http_sent_bytes=AnyInt(),
            http_sent_traces=30,
            writer_accepted_traces=30,
            buffer_accepted_traces=30,
            buffer_accepted_spans=15000,
            http_requests=1,
        )


@parametrize_with_all_encodings
@skip_if_testagent
def test_metrics_partial_flush_disabled(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    with override_global_config(dict(health_metrics_enabled=True)):
        t = Tracer()
        t.configure(
            partial_flush_enabled=False,
        )
        _test_metrics(
            t,
            http_sent_bytes=AnyInt(),
            buffer_accepted_traces=5,
            buffer_accepted_spans=15000,
            http_requests=1,
        )


@parametrize_with_all_encodings
def test_single_trace_too_large(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    assert t._partial_flush_enabled is True
    with mock.patch.object(AgentWriter, "flush_queue", return_value=None), mock.patch(
        "ddtrace.internal.writer.writer.log"
    ) as log:
        with t.trace("huge"):
            for i in range(30000):
                with t.trace("operation") as s:
                    # these strings must be unique to avoid compression
                    s.set_tag(LONG_STRING + str(i), LONG_STRING + str(i))
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
        ), log.mock_calls[:20]
        log.error.assert_not_called()
        t.shutdown()


@parametrize_with_all_encodings
@skip_if_testagent
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


@parametrize_with_all_encodings
def test_trace_generates_error_logs_when_hostname_invalid(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t.configure(hostname="bad", port=1111)

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("op").finish()
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


@parametrize_with_all_encodings
@skip_if_testagent
def test_validate_headers_in_payload_to_intake(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t._writer._put = mock.Mock(wraps=t._writer._put)
    t.trace("op").finish()
    t.shutdown()
    assert t._writer._put.call_count == 1
    headers = t._writer._put.call_args[0][1]
    assert headers.get("Datadog-Meta-Tracer-Version") == ddtrace.__version__
    assert headers.get("Datadog-Meta-Lang") == "python"
    assert headers.get("Content-Type") == "application/msgpack"
    assert headers.get("X-Datadog-Trace-Count") == "1"
    if container.get_container_info():
        assert "Datadog-Container-Id" in headers


@parametrize_with_all_encodings
@skip_if_testagent
def test_validate_headers_in_payload_to_intake_with_multiple_traces(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)
    t = Tracer()
    t._writer._put = mock.Mock(wraps=t._writer._put)
    for _ in range(100):
        t.trace("op").finish()
    t.shutdown()
    assert t._writer._put.call_count == 1
    headers = t._writer._put.call_args[0][1]
    assert headers.get("X-Datadog-Trace-Count") == "100"


@parametrize_with_all_encodings
@skip_if_testagent
def test_validate_headers_in_payload_to_intake_with_nested_spans(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)
    t = Tracer()
    t._writer._put = mock.Mock(wraps=t._writer._put)
    for _ in range(10):
        with t.trace("op"):
            for _ in range(5):
                t.trace("child").finish()
    t.shutdown()
    assert t._writer._put.call_count == 1
    headers = t._writer._put.call_args[0][1]
    assert headers.get("X-Datadog-Trace-Count") == "10"


def test_trace_with_invalid_client_endpoint_generates_error_log():
    t = Tracer()
    for client in t._writer._clients:
        client.ENDPOINT = "/bad"
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        s = t.trace("operation", service="my-svc")
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


@skip_if_testagent
def test_trace_with_invalid_payload_generates_error_log():
    log = send_invalid_payload_and_get_logs()
    log.error.assert_has_calls(
        [
            mock.call(
                "failed to send traces to intake at %s: HTTP error status %s, reason %s",
                "http://localhost:8126/v0.5/traces",
                400,
                "Bad Request",
            )
        ]
    )


@skip_if_testagent
def test_trace_with_invalid_payload_logs_payload_when_LOG_ERROR_PAYLOADS(monkeypatch):
    monkeypatch.setenv("_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS", "true")
    log = send_invalid_payload_and_get_logs()
    log.error.assert_has_calls(
        [
            mock.call(
                "failed to send traces to intake at %s: HTTP error status %s, reason %s, payload %s",
                "http://localhost:8126/v0.5/traces",
                400,
                "Bad Request",
                "6261645f7061796c6f6164",
            )
        ]
    )


@skip_if_testagent
def test_trace_with_non_bytes_payload_logs_payload_when_LOG_ERROR_PAYLOADS(monkeypatch):
    monkeypatch.setenv("_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS", "true")

    class NonBytesBadEncoder(BadEncoder):
        def encode(self):
            return u"bad_payload"

        def encode_traces(self, traces):
            return u"bad_payload"

    log = send_invalid_payload_and_get_logs(NonBytesBadEncoder)
    log.error.assert_has_calls(
        [
            mock.call(
                "failed to send traces to intake at %s: HTTP error status %s, reason %s, payload %s",
                "http://localhost:8126/v0.5/traces",
                400,
                "Bad Request",
                "bad_payload",
            )
        ]
    )


def test_trace_with_failing_encoder_generates_error_log():
    class ExceptionBadEncoder(BadEncoder):
        def encode(self):
            raise Exception()

        def encode_traces(self, traces):
            raise Exception()

    log = send_invalid_payload_and_get_logs(ExceptionBadEncoder)
    assert "failed to encode trace with encoder" in log.error.call_args[0][0]


@parametrize_with_all_encodings
@skip_if_testagent
def test_api_version_downgrade_generates_no_warning_logs(encoding, monkeypatch):
    monkeypatch.setenv("DD_TRACE_API_VERSION", encoding)

    t = Tracer()
    t._writer._downgrade(None, None, t._writer._clients[0])
    assert t._writer._endpoint == {"v0.5": "v0.4/traces", "v0.4": "v0.3/traces"}[encoding or "v0.5"]
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("operation", service="my-svc").finish()
        t.shutdown()
    log.warning.assert_not_called()
    log.error.assert_not_called()


def test_synchronous_writer_shutdown_raises_no_exception():
    tracer = Tracer()
    tracer.configure(writer=AgentWriter(tracer._writer.agent_url, sync_mode=True))
    tracer.shutdown()


@parametrize_with_all_encodings
@skip_if_testagent
def test_writer_flush_queue_generates_debug_log(caplog, encoding, monkeypatch):
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


def test_application_does_not_deadlock_when_parent_span_closes_before_child(run_python_code_in_subprocess):
    for logs_injection, debug_mode, patch_logging in itertools.product([True, False], repeat=3):
        close_parent_span_before_child = """
import ddtrace
ddtrace.patch(logging={})

s1 = ddtrace.tracer.trace("1")
s2 = ddtrace.tracer.trace("2")
s1.finish()
s2.finish()
""".format(
            str(patch_logging)
        )

        env = os.environ.copy()
        env.update(
            {
                "DD_TRACE_LOGS_INJECTION": str(logs_injection).lower(),
                "DD_TRACE_DEBUG": str(debug_mode).lower(),
                "DD_CALL_BASIC_CONFIG": "true",
            }
        )

        _, err, status, _ = run_python_code_in_subprocess(close_parent_span_before_child, env=env, timeout=5)
        assert status == 0, err


@pytest.mark.parametrize(
    "call_basic_config,debug_mode",
    itertools.permutations((True, False, None), 2),
)
def test_call_basic_config(ddtrace_run_python_code_in_subprocess, call_basic_config, debug_mode):
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
def test_writer_configured_correctly_from_env():
    import ddtrace

    assert ddtrace.tracer._writer._encoder.max_size == 1000
    assert ddtrace.tracer._writer._encoder.max_item_size == 1000
    assert ddtrace.tracer._writer._interval == 5.0


@pytest.mark.subprocess
def test_writer_configured_correctly_from_env_defaults():
    import ddtrace

    assert ddtrace.tracer._writer._encoder.max_size == 8 << 20
    assert ddtrace.tracer._writer._encoder.max_item_size == 8 << 20
    assert ddtrace.tracer._writer._interval == 1.0


def test_writer_configured_correctly_from_env_under_ddtrace_run(ddtrace_run_python_code_in_subprocess):
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


def test_writer_configured_correctly_from_env_defaults_under_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace

assert ddtrace.tracer._writer._encoder.max_size == 8 << 20
assert ddtrace.tracer._writer._encoder.max_item_size == 8 << 20
assert ddtrace.tracer._writer._interval == 1.0
""",
    )
    assert status == 0, (out, err)


@parametrize_with_all_encodings
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


def test_logging_during_tracer_init_succeeds_when_debug_logging_and_logs_injection_enabled(
    ddtrace_run_python_code_in_subprocess,
):
    env = os.environ.copy()
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_LOGS_INJECTION"] = "true"
    env["DD_CALL_BASIC_CONFIG"] = "true"

    # DEV: We don't actually have to execute any code to validate this
    out, err, status, pid = ddtrace_run_python_code_in_subprocess("", env=env)

    assert status == 0, (out, err)
    assert out == b"", "an empty program should generate no logs under ddtrace-run"

    assert (
        b"[dd.service= dd.env= dd.version= dd.trace_id=0 dd.span_id=0]" in err
    ), "stderr should contain debug output when DD_TRACE_DEBUG is set"

    assert b"KeyError: 'dd.service'" not in err, "stderr should not contain any exception logs"
    assert (
        b"ValueError: Formatting field not found in record: 'dd.service'" not in err
    ), "stderr should not contain any exception logs"


def test_no_warnings_when_Wall():
    env = os.environ.copy()
    # Have to disable sqlite3 as coverage uses it on process shutdown
    # which results in a trace being generated after the tracer shutdown
    # has been initiated which results in a deprecation warning.
    env["DD_TRACE_SQLITE3_ENABLED"] = "false"
    out, err, _, _ = call_program("ddtrace-run", sys.executable, "-Wall", "-c", "'import ddtrace'", env=env)
    assert out == b"", out
    assert err == b"", err

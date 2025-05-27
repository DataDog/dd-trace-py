# -*- coding: utf-8 -*-
import itertools
import os
import signal
import sys

import mock
import pytest

from ddtrace.internal.atexit import register_on_exit_signal
from ddtrace.internal.runtime import container
from tests.integration.utils import import_ddtrace_in_subprocess
from tests.integration.utils import parametrize_with_all_encodings
from tests.integration.utils import skip_if_testagent
from tests.utils import DummyTracer
from tests.utils import call_program


FOUR_KB = 1 << 12


@mock.patch("signal.signal")
@mock.patch("signal.getsignal")
def test_shutdown_on_exit_signal(mock_get_signal, mock_signal):
    mock_get_signal.return_value = None
    tracer = DummyTracer()
    register_on_exit_signal(tracer._atexit)
    assert mock_signal.call_count == 2
    assert mock_signal.call_args_list[0][0][0] == signal.SIGTERM
    assert mock_signal.call_args_list[1][0][0] == signal.SIGINT
    original_shutdown = tracer.shutdown
    tracer.shutdown = mock.Mock()
    mock_signal.call_args_list[0][0][1]("", "")
    assert tracer.shutdown.call_count == 1
    tracer.shutdown = original_shutdown


def test_debug_mode_generates_debug_output():
    p = import_ddtrace_in_subprocess(None)
    assert p.stdout.read() == b""
    assert b"DEBUG:ddtrace" not in p.stderr.read(), "stderr should have no debug lines when DD_TRACE_DEBUG is unset"

    env = os.environ.copy()
    env.update({"DD_TRACE_DEBUG": "true"})
    p = import_ddtrace_in_subprocess(env)
    assert p.stdout.read() == b""
    assert (
        b"debug mode has been enabled for the ddtrace logger" in p.stderr.read()
    ), "stderr should have some debug lines when DD_TRACE_DEBUG is set"


def test_import_ddtrace_generates_no_output_by_default(ddtrace_run_python_code_in_subprocess):
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace
""".lstrip()
    )
    assert err == b""
    assert out == b""
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
    assert err == b""
    assert out == b""
    assert status == 0


@pytest.mark.skip("FIXME: This test is broken. The uds socket does not exist.")
@parametrize_with_all_encodings(env={"DD_TRACE_AGENT_URL": "unix:///tmp/ddagent/trace.sock"})
def test_single_trace_uds():
    import mock

    from ddtrace.trace import tracer as t

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("client.testing").finish()
        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


@parametrize_with_all_encodings(env={"DD_TRACE_AGENT_URL": "unix:///tmp/ddagent/nosockethere"})
def test_uds_wrong_socket_path():
    import os

    import mock

    from ddtrace.trace import tracer as t

    encoding = os.environ["DD_TRACE_API_VERSION"]
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


@skip_if_testagent
@parametrize_with_all_encodings(
    env={
        "DD_TRACE_WRITER_BUFFER_SIZE_BYTES": str(FOUR_KB),
        "DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES": str(FOUR_KB),
        # use a long processing_interval to ensure a flush doesn't happen partway through
        "DD_TRACE_WRITER_INTERVAL_SECONDS": "1000",
    }
)
def test_payload_too_large():
    import os

    import mock

    from ddtrace.trace import tracer as t
    from tests.integration.test_integration import FOUR_KB
    from tests.utils import AnyInt
    from tests.utils import AnyStr

    encoding = os.environ["DD_TRACE_API_VERSION"]
    assert t._span_aggregator.writer._max_payload_size == FOUR_KB
    assert t._span_aggregator.writer._buffer_size == FOUR_KB
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


@parametrize_with_all_encodings()
def test_large_payload_is_sent_without_warning_logs():
    import mock

    from ddtrace.trace import tracer as t

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        for _ in range(10000):
            with t.trace("operation"):
                pass

        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


@parametrize_with_all_encodings()
def test_child_spans_do_not_cause_warning_logs():
    import mock

    from ddtrace.trace import tracer as t

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        spans = []
        for _ in range(10000):
            spans.append(t.trace("op"))
        for s in spans:
            s.finish()

        t.shutdown()
        log.warning.assert_not_called()
        log.error.assert_not_called()


@parametrize_with_all_encodings(env={"DD_TRACE_HEALTH_METRICS_ENABLED": "true"})
def test_metrics():
    import mock

    from ddtrace.trace import tracer as t
    from tests.utils import AnyInt
    from tests.utils import override_global_config

    assert t._span_aggregator.partial_flush_min_spans == 300

    with override_global_config(dict(_health_metrics_enabled=True)):
        statsd_mock = mock.Mock()
        t._span_aggregator.writer.dogstatsd = statsd_mock
        with mock.patch("ddtrace.internal.writer.writer.log") as log:
            for _ in range(2):
                spans = []
                for _ in range(600):
                    spans.append(t.trace("op"))
                for s in spans:
                    s.finish()

            t.shutdown()
            log.warning.assert_not_called()
            log.error.assert_not_called()

    statsd_mock.distribution.assert_has_calls(
        [
            mock.call("datadog.tracer.writer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.spans", 300, tags=None),
            mock.call("datadog.tracer.writer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.spans", 300, tags=None),
            mock.call("datadog.tracer.writer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.spans", 300, tags=None),
            mock.call("datadog.tracer.writer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.spans", 300, tags=None),
            mock.call("datadog.tracer.http.requests", 1, tags=None),
            mock.call("datadog.tracer.http.sent.bytes", AnyInt(), tags=None),
            mock.call("datadog.tracer.http.sent.bytes", AnyInt(), tags=None),
            mock.call("datadog.tracer.http.sent.traces", 4, tags=None),
        ],
        any_order=True,
    )


@parametrize_with_all_encodings(
    env={"DD_TRACE_HEALTH_METRICS_ENABLED": "true", "DD_TRACE_PARTIAL_FLUSH_ENABLED": "false"}
)
def test_metrics_partial_flush_disabled():
    import mock

    from ddtrace.trace import tracer as t
    from tests.utils import AnyInt
    from tests.utils import override_global_config

    with override_global_config(dict(_health_metrics_enabled=True)):
        statsd_mock = mock.Mock()
        t._span_aggregator.writer.dogstatsd = statsd_mock
        with mock.patch("ddtrace.internal.writer.writer.log") as log:
            for _ in range(2):
                spans = []
                for _ in range(600):
                    spans.append(t.trace("op"))
                for s in spans:
                    s.finish()

            t.shutdown()
            log.warning.assert_not_called()
            log.error.assert_not_called()

    statsd_mock.distribution.assert_has_calls(
        [
            mock.call("datadog.tracer.writer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.spans", 600, tags=None),
            mock.call("datadog.tracer.writer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.traces", 1, tags=None),
            mock.call("datadog.tracer.buffer.accepted.spans", 600, tags=None),
            mock.call("datadog.tracer.http.requests", 1, tags=None),
            mock.call("datadog.tracer.http.sent.bytes", AnyInt(), tags=None),
            mock.call("datadog.tracer.http.sent.bytes", AnyInt(), tags=None),
            mock.call("datadog.tracer.http.sent.traces", 2, tags=None),
        ],
        any_order=True,
    )


@parametrize_with_all_encodings()
def test_single_trace_too_large():
    import mock

    from ddtrace.internal.writer import AgentWriter
    from ddtrace.trace import tracer as t
    from tests.utils import AnyInt
    from tests.utils import AnyStr

    long_string = "a" * 250
    assert t._span_aggregator.partial_flush_enabled is True
    with mock.patch.object(AgentWriter, "flush_queue", return_value=None), mock.patch(
        "ddtrace.internal.writer.writer.log"
    ) as log:
        with t.trace("huge"):
            for i in range(30000):
                with t.trace("operation") as s:
                    # these strings must be unique to avoid compression
                    s.set_tag(long_string + str(i), long_string + str(i))
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


@skip_if_testagent
@parametrize_with_all_encodings(
    env={"DD_TRACE_PARTIAL_FLUSH_ENABLED": "false", "DD_TRACE_WRITER_BUFFER_SIZE_BYTES": str(8 << 20)}
)
def test_single_trace_too_large_partial_flush_disabled():
    import mock

    from ddtrace.trace import tracer as t
    from tests.utils import AnyInt

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        with t.trace("huge"):
            for _ in range(200000):
                with t.trace("operation") as s:
                    s.set_tag("a" * 10, "b" * 10)
        t.shutdown()

        calls = [mock.call("trace (%db) larger than payload buffer item limit (%db), dropping", AnyInt(), AnyInt())]
        log.warning.assert_has_calls(calls)
        log.error.assert_not_called()


@parametrize_with_all_encodings(
    env={"DD_TRACE_HEALTH_METRICS_ENABLED": "true", "DD_TRACE_AGENT_URL": "http://localhost:8125"}
)
def test_trace_generates_error_logs_when_trace_agent_url_invalid():
    import os

    import mock

    from ddtrace.trace import tracer as t

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("op").finish()
        t.shutdown()

    encoding = os.environ["DD_TRACE_API_VERSION"]
    calls = [
        mock.call(
            "failed to send, dropping %d traces to intake at %s after %d retries",
            1,
            "http://localhost:8125/{}/traces".format(encoding if encoding else "v0.5"),
            3,
        )
    ]
    log.error.assert_has_calls(calls)


@skip_if_testagent
@parametrize_with_all_encodings()
def test_validate_headers_in_payload_to_intake():
    import mock

    from ddtrace import __version__
    from ddtrace.trace import tracer as t

    t._span_aggregator.writer._put = mock.Mock(wraps=t._span_aggregator.writer._put)
    t.trace("op").finish()
    t.shutdown()
    assert t._span_aggregator.writer._put.call_count == 1
    headers = t._span_aggregator.writer._put.call_args[0][1]
    assert headers.get("Datadog-Meta-Tracer-Version") == __version__
    assert headers.get("Datadog-Meta-Lang") == "python"
    assert headers.get("Content-Type") == "application/msgpack"
    assert headers.get("X-Datadog-Trace-Count") == "1"
    if container.get_container_info():
        assert "Datadog-Container-Id" in headers
        assert "Datadog-Entity-ID" in headers
        assert headers["Datadog-Entity-ID"].startswith("ci")


@skip_if_testagent
@parametrize_with_all_encodings()
def test_inode_entity_id_header_present():
    import mock

    from ddtrace.internal.runtime import container
    from ddtrace.trace import tracer as t

    t._span_aggregator.writer._put = mock.Mock(wraps=t._span_aggregator.writer._put)
    import sys

    sys.path.append("ddtrace/internal/runtime")
    with mock.patch("container.get_container_info") as gcimock:
        from ddtrace.internal.runtime.container import CGroupInfo

        gcimock.return_value = CGroupInfo(node_inode=12345)
        t.trace("op").finish()
        t.shutdown()
    print(t._span_aggregator.writer._put.call_args)
    headers = t._span_aggregator.writer._put.call_args[-1][1]
    print(headers)
    assert "Datadog-Entity-ID" in headers
    assert headers["Datadog-Entity-ID"].startswith("in")


@skip_if_testagent
@parametrize_with_all_encodings()
def test_external_env_header_present():
    import mock

    from ddtrace.trace import tracer as t

    mocked_external_env = "it-false,cn-nginx-webserver,pu-75a2b6d5-3949-4afb-ad0d-92ff0674e759"

    t._span_aggregator.writer._put = mock.Mock(wraps=t._span_aggregator.writer._put)
    with mock.patch("os.environ.get") as oegmock:
        oegmock.return_value = mocked_external_env
        t.trace("op").finish()
        t.shutdown()
    assert t._span_aggregator.writer._put.call_count == 1
    headers = t._span_aggregator.writer._put.call_args[0][1]
    assert "Datadog-External-Env" in headers
    assert headers["Datadog-External-Env"] == mocked_external_env


@skip_if_testagent
@parametrize_with_all_encodings()
def test_validate_headers_in_payload_to_intake_with_multiple_traces():
    import mock

    from ddtrace.trace import tracer as t

    t._span_aggregator.writer._put = mock.Mock(wraps=t._span_aggregator.writer._put)
    for _ in range(100):
        t.trace("op").finish()
    t.shutdown()
    assert t._span_aggregator.writer._put.call_count == 1
    headers = t._span_aggregator.writer._put.call_args[0][1]
    assert headers.get("X-Datadog-Trace-Count") == "100"


@skip_if_testagent
@parametrize_with_all_encodings()
def test_validate_headers_in_payload_to_intake_with_nested_spans():
    import mock

    from ddtrace.trace import tracer as t

    t._span_aggregator.writer._put = mock.Mock(wraps=t._span_aggregator.writer._put)
    for _ in range(10):
        with t.trace("op"):
            for _ in range(5):
                t.trace("child").finish()
    t.shutdown()
    assert t._span_aggregator.writer._put.call_count == 1
    headers = t._span_aggregator.writer._put.call_args[0][1]
    assert headers.get("X-Datadog-Trace-Count") == "10"


@parametrize_with_all_encodings()
def test_trace_with_invalid_client_endpoint_generates_error_log():
    import mock

    from ddtrace.trace import tracer as t

    for client in t._span_aggregator.writer._clients:
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
            t._span_aggregator.writer.agent_url,
        )
    ]
    log.error.assert_has_calls(calls)


@skip_if_testagent
@pytest.mark.subprocess(err=None)
def test_trace_with_invalid_payload_generates_error_log():
    import mock

    from tests.integration.utils import send_invalid_payload_and_get_logs

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
@pytest.mark.subprocess(env={"_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS": "true", "DD_TRACE_API_VERSION": "v0.5"}, err=None)
def test_trace_with_invalid_payload_logs_payload_when_LOG_ERROR_PAYLOADS():
    import mock

    from tests.integration.utils import send_invalid_payload_and_get_logs

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
@pytest.mark.subprocess(env={"_DD_TRACE_WRITER_LOG_ERROR_PAYLOADS": "true", "DD_TRACE_API_VERSION": "v0.5"}, err=None)
def test_trace_with_non_bytes_payload_logs_payload_when_LOG_ERROR_PAYLOADS():
    import mock

    from tests.integration.utils import BadEncoder
    from tests.integration.utils import send_invalid_payload_and_get_logs

    class NonBytesBadEncoder(BadEncoder):
        def encode(self):
            return "bad_payload", 1

        def encode_traces(self, traces):
            return "bad_payload"

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


@pytest.mark.subprocess(err=None)
def test_trace_with_failing_encoder_generates_error_log():
    from tests.integration.utils import BadEncoder
    from tests.integration.utils import send_invalid_payload_and_get_logs

    class ExceptionBadEncoder(BadEncoder):
        def encode(self):
            raise Exception()

        def encode_traces(self, traces):
            raise Exception()

    log = send_invalid_payload_and_get_logs(ExceptionBadEncoder)
    assert "failed to encode trace with encoder" in log.error.call_args[0][0]


@skip_if_testagent
@pytest.mark.subprocess(err=None)
def test_api_version_downgrade_generates_no_warning_logs():
    import mock

    from ddtrace.internal.utils.http import Response
    from ddtrace.trace import tracer as t

    t._span_aggregator.writer.api_version = "v0.5"
    t._span_aggregator.writer._downgrade(Response(status=404), t._span_aggregator.writer._clients[0])
    assert t._span_aggregator.writer._endpoint == "v0.4/traces"
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("operation", service="my-svc").finish()
        t.shutdown()
    log.warning.assert_not_called()
    log.error.assert_not_called()


@skip_if_testagent
@parametrize_with_all_encodings()
def test_writer_flush_queue_generates_debug_log():
    import logging
    import os

    import mock

    from ddtrace.internal.writer import AgentWriter
    from ddtrace.settings._agent import config as agent_config
    from tests.utils import AnyFloat
    from tests.utils import AnyStr

    encoding = os.environ["DD_TRACE_API_VERSION"]
    writer = AgentWriter(agent_config.trace_agent_url)

    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        writer.write([])
        writer.flush_queue(raise_exc=True)
        calls = [
            mock.call(
                logging.DEBUG,
                "sent %s in %.5fs to %s",
                AnyStr(),
                AnyFloat(),
                "{}/{}/traces".format(writer.agent_url, encoding),
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
            }
        )

        _, err, status, _ = run_python_code_in_subprocess(close_parent_span_before_child, env=env, timeout=5)
        assert status == 0, err


@pytest.mark.subprocess(
    env=dict(
        DD_TRACE_WRITER_BUFFER_SIZE_BYTES="1000",
        DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES="5000",
        DD_TRACE_WRITER_INTERVAL_SECONDS="5.0",
    )
)
def test_writer_configured_correctly_from_env():
    import ddtrace

    assert ddtrace.tracer._span_aggregator.writer._encoder.max_size == 1000
    assert ddtrace.tracer._span_aggregator.writer._encoder.max_item_size == 1000
    assert ddtrace.tracer._span_aggregator.writer._interval == 5.0


@pytest.mark.subprocess
def test_writer_configured_correctly_from_env_defaults():
    import ddtrace

    assert ddtrace.tracer._span_aggregator.writer._encoder.max_size == 20 << 20
    assert ddtrace.tracer._span_aggregator.writer._encoder.max_item_size == 20 << 20
    assert ddtrace.tracer._span_aggregator.writer._interval == 1.0


def test_writer_configured_correctly_from_env_under_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    env["DD_TRACE_WRITER_BUFFER_SIZE_BYTES"] = "1000"
    env["DD_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES"] = "5000"
    env["DD_TRACE_WRITER_INTERVAL_SECONDS"] = "5.0"

    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace

assert ddtrace.tracer._span_aggregator.writer._encoder.max_size == 1000
assert ddtrace.tracer._span_aggregator.writer._encoder.max_item_size == 1000
assert ddtrace.tracer._span_aggregator.writer._interval == 5.0
""",
        env=env,
    )
    assert status == 0, (out, err)


def test_writer_configured_correctly_from_env_defaults_under_ddtrace_run(ddtrace_run_python_code_in_subprocess):
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import ddtrace

assert ddtrace.tracer._span_aggregator.writer._encoder.max_size == 20 << 20
assert ddtrace.tracer._span_aggregator.writer._encoder.max_item_size == 20 << 20
assert ddtrace.tracer._span_aggregator.writer._interval == 1.0
""",
    )
    assert status == 0, (out, err)


@parametrize_with_all_encodings(env={"DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "2"})
def test_partial_flush_log():
    import mock

    from ddtrace.trace import tracer as t

    partial_flush_min_spans = 2

    s1 = t.trace("1")
    s2 = t.trace("2")
    s3 = t.trace("3")
    t_id = s3.trace_id

    with mock.patch("ddtrace._trace.processor.log") as log:
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

    # DEV: We don't actually have to execute any code to validate this
    out, err, status, pid = ddtrace_run_python_code_in_subprocess("", env=env)

    assert status == 0, (out, err)
    assert out == b"", "an empty program should generate no logs under ddtrace-run"

    assert (
        b"[dd.service=ddtrace_subprocess_dir dd.env= dd.version= dd.trace_id=0 dd.span_id=0]" in err
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

# -*- coding: utf-8 -*-
import logging
import os
import signal

import mock
import pytest

from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.trace import tracer
from tests.integration.utils import AGENT_VERSION
from tests.utils import override_global_config
from tests.utils import snapshot


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")


@snapshot(include_tracer=True)
@pytest.mark.subprocess()
def test_single_trace_single_span(tracer):
    from ddtrace.trace import tracer

    s = tracer.trace("operation", service="my-svc")
    s.set_tag("k", "v")
    # numeric tag
    s.set_tag("num", 1234)
    s.set_metric("float_metric", 12.34)
    s.set_metric("int_metric", 4321)
    s.finish()
    tracer.flush()


@pytest.mark.subprocess()
@pytest.mark.snapshot()
def test_flush_spans_before_writer_recreate():
    """
    Test that spans are flushed before the writer is recreated.
    This is to ensure that spans are not lost when the writer is recreated.
    """
    from ddtrace.trace import tracer

    # Create a span that will be queued by the writer before the writer is recreated
    with tracer.trace("operation", service="my-svc"):
        pass
    # Create a long running span that will be finished after the writer is recreated
    long_running_span = tracer.trace("long_running_operation")

    writer = tracer._span_aggregator.writer
    # Enable compute stats to trigger the recreation of the agent writer
    tracer._recreate(reset_buffer=False)
    assert tracer._span_aggregator.writer is not writer, "Writer should be recreated"
    # Finish the long running span after the writer has been recreated
    long_running_span.finish()


@snapshot(include_tracer=True)
@pytest.mark.subprocess()
def test_multiple_traces(tracer):
    from ddtrace.trace import tracer

    with tracer.trace("operation1", service="my-svc") as s:
        s.set_tag("k", "v")
        s.set_tag("num", 1234)
        s.set_metric("float_metric", 12.34)
        s.set_metric("int_metric", 4321)
        tracer.trace("child").finish()

    with tracer.trace("operation2", service="my-svc") as s:
        s.set_tag("k", "v")
        s.set_tag("num", 1234)
        s.set_metric("float_metric", 12.34)
        s.set_metric("int_metric", 4321)
        tracer.trace("child").finish()
    tracer.flush()


@snapshot(include_tracer=True)
@pytest.mark.subprocess(
    token="tests.integration.test_integration_snapshots.test_filters",
)
def test_filters():
    from ddtrace.trace import TraceFilter
    from ddtrace.trace import tracer

    class FilterMutate(TraceFilter):
        def __init__(self, key, value):
            self.key = key
            self.value = value

        def process_trace(self, trace):
            for s in trace:
                s.set_tag(self.key, self.value)
            return trace

    tracer.configure(trace_processors=[FilterMutate("boop", "beep")])

    with tracer.trace("root"):
        with tracer.trace("child"):
            pass
    tracer.flush()


# Have to use sync mode snapshot so that the traces are associated to this
# test case since we use a custom writer (that doesn't have the trace headers
# injected).
@pytest.mark.subprocess(parametrize={"writer_class": ["AgentWriter", "NativeWriter"]})
@snapshot(async_mode=False)
def test_synchronous_writer(writer_class):
    import os

    from ddtrace.internal.writer import AgentWriter
    from ddtrace.internal.writer import NativeWriter
    from ddtrace.trace import tracer

    if os.environ["writer_class"] == "AgentWriter":
        writer_class = AgentWriter
    elif os.environ["writer_class"] == "NativeWriter":
        writer_class = NativeWriter

    writer = writer_class(tracer._span_aggregator.writer.intake_url, sync_mode=True)
    tracer._span_aggregator.writer = writer
    tracer._recreate()
    with tracer.trace("operation1", service="my-svc"):
        with tracer.trace("child1"):
            pass

    with tracer.trace("operation2", service="my-svc"):
        with tracer.trace("child2"):
            pass


@pytest.mark.skipif(
    PYTHON_VERSION_INFO >= (3, 14),
    reason="The default multiprocessing start_method 'forkserver' causes this test to fail",
)
@snapshot(async_mode=False)
@pytest.mark.subprocess(ddtrace_run=True)
def test_tracer_trace_across_popen():
    """
    When a trace is started in a parent process and a child process is spawned
        The trace should be continued in the child process.
    """
    import multiprocessing

    from ddtrace import tracer

    def task(tracer):
        import ddtrace.auto  # noqa

        with tracer.trace("child"):
            pass
        tracer.flush()

    with tracer.trace("parent"):
        p = multiprocessing.Process(target=task, args=(tracer,))
        p.start()
        p.join()

    tracer.flush()


@pytest.mark.skipif(
    PYTHON_VERSION_INFO >= (3, 14),
    reason="The default multiprocessing start_method 'forkserver' causes this test to fail",
)
@snapshot(async_mode=False)
@pytest.mark.subprocess(ddtrace_run=True)
def test_tracer_trace_across_multiple_popens():
    """
    When a trace is started and crosses multiple process boundaries
        The trace should be continued in the child processes.
    """
    import multiprocessing

    from ddtrace.trace import tracer

    def task(tracer):
        import ddtrace.auto  # noqa

        def task2(tracer):
            import ddtrace.auto  # noqa

            with tracer.trace("child2"):
                pass
            tracer.flush()

        with tracer.trace("child1"):
            p = multiprocessing.Process(target=task2, args=(tracer,))
            p.start()
            p.join()
        tracer.flush()

    with tracer.trace("parent"):
        p = multiprocessing.Process(target=task, args=(tracer,))
        p.start()
        p.join()
    tracer.flush()


@snapshot()
@pytest.mark.subprocess()
def test_wrong_span_name_type_not_sent():
    """Span names should be a text type.

    When an invalid type is passed, the span is created with an empty name
    instead of raising an error (graceful degradation).
    """
    from ddtrace.trace import tracer

    # Should not raise - instead creates span with empty name
    with tracer.trace(123) as span:
        # Invalid type gets coerced to empty string
        assert span.name == ""


@snapshot()
@pytest.mark.subprocess()
def test_wrong_service_type_not_sent():
    """Span service should be a text type.

    When an invalid type is passed, the span is created with an empty service
    instead of raising an error (graceful degradation).
    """
    from ddtrace.trace import tracer

    # Should not raise - instead creates span with empty service
    with tracer.trace("test.span", service=456) as span:
        # Invalid type gets coerced to empty string
        assert span.service is None


@pytest.mark.parametrize(
    "meta",
    [
        ({"env": "my-env", "tag1": "some_str_1", "tag2": "some_str_2", "tag3": [1, 2, 3]}),
        ({"env": "test-env", b"tag1": {"wrong_type": True}, b"tag2": "some_str_2", b"tag3": "some_str_3"}),
        ({"env": "my-test-env", "😐": "some_str_1", b"tag2": "some_str_2", "unicode": 12345}),
        ({"env": set([1, 2, 3])}),
        ({"env": None}),
        ({"env": True}),
        ({"env": 1.0}),
    ],
)
@pytest.mark.parametrize("encoding", ["v0.4", "v0.5"])
def test_trace_with_wrong_meta_types_not_sent(encoding, meta, monkeypatch):
    """Wrong meta types should raise TypeErrors during encoding and fail to send to the agent."""
    with override_global_config(dict(_trace_api=encoding)):
        logger = logging.getLogger("ddtrace.internal._encoding")
        with mock.patch.object(logger, "warning") as log_warning:
            with tracer.trace("root") as root:
                root._meta = meta
                for _ in range(299):
                    with tracer.trace("child") as child:
                        child._meta = meta

            assert log_warning.call_count == 300
            log_warning.assert_called_with(
                "[span ID %d] Meta key %r has non-string value %r, skipping", mock.ANY, mock.ANY, mock.ANY
            )


@pytest.mark.parametrize(
    "metrics,expected_warning_count",
    [
        ({"num1": 12345, "num2": 53421, "num3": 1, "num4": "not-a-number"}, 300),
        ({b"num1": 123.45, b"num2": [1, 2, 3], b"num3": 11.0, b"num4": 1.20}, 300),
        ({"😐": "123.45", b"num2": "1", "num3": {"is_number": False}, "num4": "12345"}, 1200),
    ],
)
@pytest.mark.parametrize("encoding", ["v0.4", "v0.5"])
def test_trace_with_wrong_metrics_types_not_sent(encoding, metrics, expected_warning_count):
    """Wrong metric types should raise TypeErrors during encoding and fail to send to the agent."""
    with override_global_config(dict(_trace_api=encoding)):
        logger = logging.getLogger("ddtrace.internal._encoding")
        with mock.patch.object(logger, "warning") as log_warning:
            with tracer.trace("root") as root:
                root._metrics = metrics
                for _ in range(299):
                    with tracer.trace("child") as child:
                        child._metrics = metrics

            assert log_warning.call_count == expected_warning_count
            log_warning.assert_called_with(
                "[span ID %d] Metric key %r has non-numeric value %r, skipping", mock.ANY, mock.ANY, mock.ANY
            )


@pytest.mark.subprocess()
@pytest.mark.snapshot()
def test_tracetagsprocessor_only_adds_new_tags():
    from ddtrace.constants import _SAMPLING_PRIORITY_KEY
    from ddtrace.constants import AUTO_KEEP
    from ddtrace.constants import USER_KEEP
    from ddtrace.trace import tracer

    with tracer.trace(name="web.request") as span:
        span.context.sampling_priority = AUTO_KEEP
        span.set_metric(_SAMPLING_PRIORITY_KEY, USER_KEEP)

    tracer.flush()


# Override the token so that both parameterizations of the test use the same snapshot
# (The snapshots should be equivalent)
@snapshot(token_override="tests.integration.test_integration_snapshots.test_env_vars")
@pytest.mark.parametrize("use_ddtracerun", [True, False])
def test_env_vars(use_ddtracerun, ddtrace_run_python_code_in_subprocess, run_python_code_in_subprocess):
    """Ensure environment variable config is respected by ddtrace-run usages as well as regular."""
    if use_ddtracerun:
        fn = ddtrace_run_python_code_in_subprocess
    else:
        fn = run_python_code_in_subprocess

    env = os.environ.copy()
    env.update(
        dict(
            DD_ENV="prod",
            DD_SERVICE="my-svc",
            DD_VERSION="1234",
        )
    )

    fn(
        """
import ddtrace.auto

from ddtrace.trace import tracer
tracer.trace("test-op").finish()
""",
        env=env,
    )


@pytest.mark.snapshot(token="tests.integration.test_integration_snapshots.test_snapshot_skip")
def test_snapshot_skip():
    pytest.skip("Test that snapshot tests can be skipped")
    with tracer.trace("test"):
        pass


@pytest.mark.parametrize("encoding", ["v0.4", "v0.5"])
@pytest.mark.snapshot()
def test_setting_span_tags_and_metrics_generates_no_error_logs(encoding):
    from ddtrace import tracer

    with override_global_config(dict(_trace_api=encoding)):
        s = tracer.trace("operation", service="my-svc")
        s.set_tag("env", "my-env")
        s.set_metric("number1", 123)
        s.set_metric("number2", 12.0)
        s.set_metric("number3", "1")
        s.finish()


@pytest.mark.parametrize("encoding", ["v0.4", "v0.5"])
@pytest.mark.snapshot()
def test_encode_span_with_large_string_attributes(encoding):
    from ddtrace import tracer

    with override_global_config(dict(_trace_api=encoding)):
        with tracer.trace(name="a" * 25000, resource="b" * 25001) as span:
            span.set_tag(key="c" * 25001, value="d" * 2000)


@pytest.mark.parametrize("encoding", ["v0.4", "v0.5"])
@pytest.mark.snapshot()
def test_encode_span_with_large_unicode_string_attributes(encoding):
    from ddtrace import tracer

    with override_global_config(dict(_trace_api=encoding)):
        with tracer.trace(name="á" * 25000, resource="â" * 25001) as span:
            span.set_tag(key="å" * 25001, value="ä" * 2000)


@pytest.mark.snapshot
@pytest.mark.subprocess(env={"DD_TRACE_PARTIAL_FLUSH_ENABLED": "true", "DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "1"})
def test_aggregator_partial_flush_finished_counter_out_of_sync():
    """Regression test for IndexError when num_finished counter is out of sync with finished spans."""
    from ddtrace import tracer

    span1 = tracer.start_span("span1")
    span2 = tracer.start_span("span2", child_of=span1)
    # Manually set duration_ns before calling finish() to trigger the race condition
    # where trace.num_finished == 1 but len(trace.spans) == 0 after span1.finish().
    # In this scenario, span1.finish() does NOT trigger encoding, both span1 and span2
    # are encoded during span2.finish(). This occurs because _Trace.remove_finished()
    # removes all spans that have a duration (end state). This test ensures that calling
    # span1.finish() in this state does not cause unexpected behavior or crash trace encoding.
    span1.duration_ns = 1
    span2.finish()
    span1.finish()


@pytest.mark.parametrize("use_ddtrace_run", [True, False])
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize("writer_class", ["AgentWriter", "NativeWriter"])
@pytest.mark.parametrize("api_version", ["v0.4", "v0.5"])
@pytest.mark.parametrize("compute_stats", ["false", "true"])
def test_signal_shutdown_flushes_traces(
    use_ddtrace_run,
    signum,
    writer_class,
    api_version,
    compute_stats,
    tmpdir,
    ddtrace_run_python_code_in_subprocess,
    run_python_code_in_subprocess,
    snapshot_context,
):
    """
    Regression test: Ensure traces are flushed when a process receives SIGTERM or SIGINT.

    The subprocess creates a trace, signals it's ready, then waits. The parent sends
    the specified signal, and the tracer should flush traces before exiting.
    """
    import subprocess
    import sys

    code = """
import time

from ddtrace.trace import tracer

with tracer.trace("signal-shutdown-test", service="signal-test-svc"):
    pass

# Give NativeWriter time to fully initialize before signaling ready
# NativeWriter has startup overhead (especially with stats enabled) that can cause
# a race condition if the parent sends a signal before initialization completes
try:
    time.sleep(0.25)
    print("READY", flush=True)

    # Busy loop waiting to get killed by parent
    # time.sleep(30) sometimes causes the process to block the signal handling
    while True:
        time.sleep(0.1)
except KeyboardInterrupt:
    # Don't crash on SIGINT
    pass
"""

    # Write code to temp file
    pyfile = tmpdir.join("test_signal_shutdown.py")
    pyfile.write(code)

    # Build command
    cmd = [sys.executable, str(pyfile)]
    if use_ddtrace_run:
        cmd = ["ddtrace-run"] + cmd

    # Use different snapshot tokens for stats vs nostats since stats sends additional payloads
    token = "tests.integration.test_integration_snapshots.test_signal_shutdown_flushes_traces"
    variants = {
        "nostats": compute_stats == "false",
        "stats": compute_stats == "true",
    }

    with snapshot_context(
        token=token,
        ignores=["meta._dd.base_service"],
        variants=variants,
    ):
        # Copy environment INSIDE snapshot_context so it includes the test session token
        env = os.environ.copy()
        env.update(
            {
                "DD_TRACE_WRITER_INTERVAL_SECONDS": "30",  # High interval to prevent auto-flush
                "DD_TRACE_API_VERSION": api_version,
                "DD_TRACE_COMPUTE_STATS": compute_stats,
                "_DD_TRACE_WRITER_NATIVE": "true" if writer_class == "NativeWriter" else "false",
            }
        )

        # Start subprocess - capture stderr to see what it's sending
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # Capture stderr instead of passing through
        )

        try:
            ready = False
            while True:
                line = proc.stdout.readline()
                if not line:
                    break

                if b"READY" in line:
                    ready = True
                    break

            if not ready:
                stderr = proc.stderr.read()
                pytest.fail(f"Subprocess did not signal ready. Got: {line!r}, stderr: {stderr.decode()}")

            # Send the signal
            os.kill(proc.pid, signum)

            # Wait for process to exit (should flush traces during shutdown)
            # Tracer has SHUTDOWN_TIMEOUT=5s, allow some extra time for test agent communication
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                pytest.fail("Process did not exit after SIGTERM")

        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

# -*- coding: utf-8 -*-
import multiprocessing
import os

import mock
import pytest

from ddtrace import Tracer
from ddtrace import config
from ddtrace import tracer
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.constants import USER_KEEP
from ddtrace.internal.writer import AgentWriter
from tests.integration.utils import mark_snapshot
from tests.integration.utils import parametrize_with_all_encodings
from tests.utils import override_global_config
from tests.utils import snapshot

from .test_integration import AGENT_VERSION


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")


@snapshot(include_tracer=True)
def test_single_trace_single_span(tracer):
    s = tracer.trace("operation", service="my-svc")
    s.set_tag("k", "v")
    # numeric tag
    s.set_tag("num", 1234)
    s.set_metric("float_metric", 12.34)
    s.set_metric("int_metric", 4321)
    s.finish()
    tracer.shutdown()


@snapshot(include_tracer=True)
def test_multiple_traces(tracer):
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
    tracer.shutdown()


@pytest.mark.parametrize(
    "writer",
    ("default", "sync"),
)
@snapshot(include_tracer=True)
def test_filters(writer, tracer):
    if writer == "sync":
        writer = AgentWriter(
            tracer.agent_trace_url,
            priority_sampling=config._priority_sampling,
            sync_mode=True,
        )
        # Need to copy the headers which contain the test token to associate
        # traces with this test case.
        writer._headers = tracer._writer._headers
    else:
        writer = tracer._writer

    class FilterMutate(object):
        def __init__(self, key, value):
            self.key = key
            self.value = value

        def process_trace(self, trace):
            for s in trace:
                s.set_tag(self.key, self.value)
            return trace

    tracer.configure(
        settings={
            "FILTERS": [FilterMutate("boop", "beep")],
        },
        writer=writer,
    )

    with tracer.trace("root"):
        with tracer.trace("child"):
            pass
    tracer.shutdown()


# Have to use sync mode snapshot so that the traces are associated to this
# test case since we use a custom writer (that doesn't have the trace headers
# injected).
@snapshot(async_mode=False)
def test_synchronous_writer():
    tracer = Tracer()
    writer = AgentWriter(tracer._writer.agent_url, sync_mode=True, priority_sampling=config._priority_sampling)
    tracer.configure(writer=writer)
    with tracer.trace("operation1", service="my-svc"):
        with tracer.trace("child1"):
            pass

    with tracer.trace("operation2", service="my-svc"):
        with tracer.trace("child2"):
            pass


@snapshot(async_mode=False)
def test_tracer_trace_across_popen():
    """
    When a trace is started in a parent process and a child process is spawned
        The trace should be continued in the child process. The fact that
        the child span has does not have '_dd.p.dm' shows that sampling was run
        before fork automatically.
    """
    tracer = Tracer()

    def task(tracer):
        with tracer.trace("child"):
            pass
        tracer.shutdown()

    with tracer.trace("parent"):
        p = multiprocessing.Process(target=task, args=(tracer,))
        p.start()
        p.join()

    tracer.shutdown()


@snapshot(async_mode=False)
def test_tracer_trace_across_multiple_popens():
    """
    When a trace is started and crosses multiple process boundaries
        The trace should be continued in the child processes. The fact that
        the child span has does not have '_dd.p.dm' shows that sampling was run
        before fork automatically.
    """
    tracer = Tracer()

    def task(tracer):
        def task2(tracer):
            with tracer.trace("child2"):
                pass
            tracer.shutdown()

        with tracer.trace("child1"):
            p = multiprocessing.Process(target=task2, args=(tracer,))
            p.start()
            p.join()
        tracer.shutdown()

    with tracer.trace("parent"):
        p = multiprocessing.Process(target=task, args=(tracer,))
        p.start()
        p.join()
    tracer.shutdown()


@snapshot()
def test_wrong_span_name_type_not_sent():
    """Span names should be a text type."""
    tracer = Tracer()
    with mock.patch("ddtrace._trace.span.log") as log:
        with tracer.trace(123):
            pass
        log.exception.assert_called_once_with("error closing trace")


@pytest.mark.parametrize(
    "meta",
    [
        ({"env": "my-env", "tag1": "some_str_1", "tag2": "some_str_2", "tag3": [1, 2, 3]}),
        ({"env": "test-env", b"tag1": {"wrong_type": True}, b"tag2": "some_str_2", b"tag3": "some_str_3"}),
        ({"env": "my-test-env", "😐": "some_str_1", b"tag2": "some_str_2", "unicode": 12345}),
    ],
)
@pytest.mark.parametrize("encoding", ["v0.4", "v0.5"])
@snapshot()
def test_trace_with_wrong_meta_types_not_sent(encoding, meta, monkeypatch):
    """Wrong meta types should raise TypeErrors during encoding and fail to send to the agent."""
    with override_global_config(dict(_trace_api=encoding)):
        tracer = Tracer()
        with mock.patch("ddtrace._trace.span.log") as log:
            with tracer.trace("root") as root:
                root._meta = meta
                for _ in range(299):
                    with tracer.trace("child") as child:
                        child._meta = meta
            log.exception.assert_called_once_with("error closing trace")


@pytest.mark.parametrize(
    "metrics",
    [
        ({"num1": 12345, "num2": 53421, "num3": 1, "num4": "not-a-number"}),
        ({b"num1": 123.45, b"num2": [1, 2, 3], b"num3": 11.0, b"num4": 1.20}),
        ({"😐": "123.45", b"num2": "1", "num3": {"is_number": False}, "num4": "12345"}),
    ],
)
@pytest.mark.parametrize("encoding", ["v0.4", "v0.5"])
@snapshot()
def test_trace_with_wrong_metrics_types_not_sent(encoding, metrics, monkeypatch):
    """Wrong metric types should raise TypeErrors during encoding and fail to send to the agent."""
    with override_global_config(dict(_trace_api=encoding)):
        tracer = Tracer()
        with mock.patch("ddtrace._trace.span.log") as log:
            with tracer.trace("root") as root:
                root._metrics = metrics
                for _ in range(299):
                    with tracer.trace("child") as child:
                        child._metrics = metrics
            log.exception.assert_called_once_with("error closing trace")


@snapshot()
def test_tracetagsprocessor_only_adds_new_tags():
    tracer = Tracer()
    with tracer.trace(name="web.request") as span:
        span.context.sampling_priority = AUTO_KEEP
        span.set_metric(SAMPLING_PRIORITY_KEY, USER_KEEP)

    tracer.shutdown()


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
from ddtrace import tracer
tracer.trace("test-op").finish()
""",
        env=env,
    )


@pytest.mark.snapshot(token="tests.integration.test_integration_snapshots.test_snapshot_skip")
def test_snapshot_skip():
    pytest.skip("Test that snapshot tests can be skipped")
    with tracer.trace("test"):
        pass


@parametrize_with_all_encodings
@mark_snapshot
def test_setting_span_tags_and_metrics_generates_no_error_logs():
    import ddtrace

    s = ddtrace.tracer.trace("operation", service="my-svc")
    s.set_tag("env", "my-env")
    s.set_metric("number1", 123)
    s.set_metric("number2", 12.0)
    s.set_metric("number3", "1")
    s.finish()

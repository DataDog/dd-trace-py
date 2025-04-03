import threading
import time

import opentelemetry
from opentelemetry.baggage import get_baggage
from opentelemetry.baggage import remove_baggage
from opentelemetry.baggage import set_baggage
from opentelemetry.context import attach
import pytest

import ddtrace
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY


@pytest.mark.snapshot(wait_for_num_traces=1)
def test_otel_span_parenting(oteltracer):
    with oteltracer.start_as_current_span("otel-root") as root:
        time.sleep(0.02)
        with oteltracer.start_as_current_span("otel-parent1"):
            time.sleep(0.04)
            with oteltracer.start_as_current_span("otel-child1"):
                time.sleep(0.06)

        orphan1 = oteltracer.start_span("orphan1", context=None)

        ctx = opentelemetry.trace.set_span_in_context(opentelemetry.trace.NonRecordingSpan(root.get_span_context()))
        with oteltracer.start_span("otel-parent2", context=ctx) as parent2:
            time.sleep(0.04)
            ctx = opentelemetry.trace.set_span_in_context(
                opentelemetry.trace.NonRecordingSpan(parent2.get_span_context())
            )
            with oteltracer.start_as_current_span("otel-child2", context=ctx):
                time.sleep(0.06)

        orphan1.end()


@pytest.mark.snapshot(wait_for_num_traces=1)
def test_otel_ddtrace_mixed_parenting(oteltracer):
    with oteltracer.start_as_current_span("otel-top-level"):
        with ddtrace.tracer.trace("ddtrace-top-level"):
            time.sleep(0.02)
            with ddtrace.tracer.trace("ddtrace-child"):
                time.sleep(0.04)

            with oteltracer.start_as_current_span("otel-child"):
                time.sleep(0.02)
                with ddtrace.tracer.trace("ddtrace-grandchild"):
                    with oteltracer.start_as_current_span("otel-grandchild"):
                        time.sleep(0.02)


@pytest.mark.snapshot(wait_for_num_traces=1)
def test_otel_multithreading(oteltracer):
    def target(parent_context):
        ctx = opentelemetry.trace.set_span_in_context(opentelemetry.trace.NonRecordingSpan(parent_context))
        with oteltracer.start_as_current_span("s1", context=ctx):
            with oteltracer.start_as_current_span("s2"):
                time.sleep(0.02)
            with oteltracer.start_as_current_span("s3"):
                time.sleep(0.06)

    with oteltracer.start_span("otel-threading-root") as root:
        # Opentelemetry does not automatically propagate a span context across threads.
        # https://github.com/open-telemetry/opentelemetry-python-contrib/issues/737#issuecomment-1072763764
        ts = [threading.Thread(target=target, args=(root.get_span_context(),)) for _ in range(4)]
        for t in ts:
            t.start()

        for t in ts:
            t.join()


def _subprocess_task(parent_span_context, errors):
    import ddtrace.auto  # noqa

    from ddtrace.opentelemetry import TracerProvider

    # Tracer provider must be set in the subprocess otherwise the default tracer will be used
    opentelemetry.trace.set_tracer_provider(TracerProvider())
    ot_tracer = opentelemetry.trace.get_tracer(__name__)
    try:
        ctx = opentelemetry.trace.set_span_in_context(opentelemetry.trace.NonRecordingSpan(parent_span_context))
        with ot_tracer.start_as_current_span("task", context=ctx):
            time.sleep(0.02)
    except AssertionError as e:
        errors.put(e)
    finally:
        # Process.terminate() send a termination signal which skips the execution of exit handlers.
        # We must flush all traces before the process is killed.
        ot_tracer._tracer.flush()


@pytest.mark.snapshot(wait_for_num_traces=1, ignores=["meta.tracestate"])
@pytest.mark.subprocess(env={"DD_TRACE_OTEL_ENABLED": "true"}, ddtrace_run=True, err=None)
def test_otel_trace_across_fork():
    import multiprocessing

    from opentelemetry.trace import get_tracer

    from tests.opentelemetry.test_context import _subprocess_task

    oteltracer = get_tracer(__name__)

    errors = multiprocessing.Queue()
    with oteltracer.start_as_current_span("root") as root:
        oteltracer._tracer.sample(root._ddspan)
        p = multiprocessing.Process(target=_subprocess_task, args=(root.get_span_context(), errors))
        try:
            p.start()
        finally:
            p.join(timeout=2)

    assert errors.empty(), errors.get()


@pytest.mark.snapshot(wait_for_num_traces=1, ignores=["meta.tracestate"])
@pytest.mark.subprocess(
    env={"DD_TRACE_OTEL_ENABLED": "true"},
    parametrize={"SAMPLING_DECISION": [MANUAL_KEEP_KEY, MANUAL_DROP_KEY]},
    ddtrace_run=True,
    err=None,
)
def test_sampling_decisions_across_processes():
    # sampling decision in the subprocess task should be the same as the parent
    import multiprocessing
    import os

    from opentelemetry.trace import get_tracer

    from tests.opentelemetry.test_context import _subprocess_task

    decision = os.environ["SAMPLING_DECISION"]
    oteltracer = get_tracer(__name__)

    errors = multiprocessing.Queue()
    with oteltracer.start_as_current_span("root", attributes={decision: ""}) as root:
        p = multiprocessing.Process(target=_subprocess_task, args=(root.get_span_context(), errors))
        try:
            p.start()
        finally:
            p.join(timeout=2)

        assert errors.empty(), errors.get()


@pytest.mark.asyncio
@pytest.mark.snapshot(wait_for_num_traces=1)
async def test_otel_trace_multiple_coroutines(oteltracer):
    async def coro(i):
        with oteltracer.start_as_current_span("corountine %s" % (i,)):
            time.sleep(0.02)
            return 42

    with oteltracer.start_as_current_span("root"):
        await coro(1)
        await coro(2)
        await coro(3)
        await coro(4)


def test_otel_baggage_propagation_to_ddtrace(oteltracer):
    with oteltracer.start_as_current_span("otel-baggage-inject") as span:  # noqa: F841
        baggage_context = set_baggage("key1", "value1")
        baggage_context = set_baggage("key2", "value2", baggage_context)
        attach(baggage_context)
        with ddtrace.tracer.trace("ddtrace-baggage-inject") as ddspan:
            assert ddspan.context.get_baggage_item("key1") == "value1"
            assert ddspan.context.get_baggage_item("key2") == "value2"


def test_ddtrace_baggage_propagation_to_otel(oteltracer):
    with ddtrace.tracer.trace("ddtrace-baggage") as ddspan:
        ddspan.context.set_baggage_item("key1", "value1")
        ddspan.context.set_baggage_item("key2", "value2")
        assert get_baggage("key1") == "value1"
        assert get_baggage("key2") == "value2"


def test_conflicting_otel_and_ddtrace_baggage(oteltracer):
    with ddtrace.tracer.trace("ddtrace-baggage") as ddspan:
        ddspan.context.set_baggage_item("key1", "dd1")
        attach(set_baggage("key1", "otel1"))
        attach(set_baggage("key2", "otel2"))
        ddspan.context.set_baggage_item("key2", "dd2")
        assert get_baggage("key1") == "otel1"
        assert get_baggage("key2") == "dd2"


def test_otel_baggage_removal_propagation_to_ddtrace(oteltracer):
    with oteltracer.start_as_current_span("otel-baggage-inject") as span:  # noqa: F841
        baggage_context = set_baggage("key1", "value1")
        baggage_context = set_baggage("key2", "value2", baggage_context)
        attach(baggage_context)
        baggage_context = set_baggage("key3", "value3")
        baggage_context = set_baggage("key4", "value4", baggage_context)
        baggage_context = remove_baggage("key1", baggage_context)
        baggage_context = remove_baggage("key2", baggage_context)
        attach(baggage_context)
        with ddtrace.tracer.trace("ddtrace-baggage-inject") as ddspan:
            # newest baggage set in otel should take precedence
            assert ddspan.context.get_baggage_item("key3") == "value3"
            assert ddspan.context.get_baggage_item("key4") == "value4"
            assert ddspan.context.get_baggage_item("key1") is None
            assert ddspan.context.get_baggage_item("key2") is None

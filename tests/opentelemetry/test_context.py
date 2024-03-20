import multiprocessing
import threading
import time

import opentelemetry
import pytest

import ddtrace
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from tests.utils import flaky


@pytest.mark.snapshot
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


@flaky(1735812000)
@pytest.mark.snapshot
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


@pytest.mark.snapshot
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


@pytest.mark.snapshot(ignores=["meta.tracestate"])
def test_otel_trace_across_fork(oteltracer):
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
@pytest.mark.parametrize("decision", [MANUAL_KEEP_KEY, MANUAL_DROP_KEY], ids=["manual.keep", "manual.drop"])
def test_sampling_decisions_across_processes(oteltracer, decision):
    # sampling decision in the subprocess task should be the same as the parent
    errors = multiprocessing.Queue()
    with oteltracer.start_as_current_span("root", attributes={decision: ""}) as root:
        p = multiprocessing.Process(target=_subprocess_task, args=(root.get_span_context(), errors))
        try:
            p.start()
        finally:
            p.join(timeout=2)

        assert errors.empty(), errors.get()


@pytest.mark.asyncio
@pytest.mark.snapshot
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

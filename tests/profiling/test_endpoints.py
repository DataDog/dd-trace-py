import gc
import weakref

import pytest

from ddtrace.internal import core
from ddtrace.internal.datadog.profiling import endpoints
from ddtrace.internal.span_lifecycle import CompletedSpan
from ddtrace.internal.span_lifecycle import SpanCompletedEvent


@pytest.fixture(autouse=True)
def isolated_endpoint_collection(tracer):
    # Collector tests share the global tracer and may explicitly enable endpoint collection.
    endpoints.disable(tracer)
    try:
        yield
    finally:
        endpoints.disable(tracer)


@pytest.fixture
def counter(tracer):
    endpoints.enable(tracer)
    try:
        yield tracer
    finally:
        endpoints.disable(tracer)


def test_endpoint_counts_use_completed_local_root_web_spans(counter):
    tracer = counter
    with tracer.trace("root", resource="a", span_type="web") as root:
        with tracer.trace("child", resource="ignored", span_type="web"):
            pass
    root.finish()
    with tracer.trace("root", resource="a", span_type="web") as second:
        pass
    with tracer.trace("root", resource="b", span_type="web") as third:
        pass
    with tracer.trace("outbound", resource="ignored", span_type="http"):
        pass
    assert endpoints.reset(tracer) == (
        {"a": 2, "b": 1},
        {"a": [root.span_id, second.span_id], "b": [third.span_id]},
    )
    assert endpoints.reset(tracer) == ({}, {})


def test_endpoint_collection_is_disabled_by_default(tracer):
    with tracer.trace("root", resource="a", span_type="web"):
        pass
    assert endpoints.reset(tracer) == ({}, {})


def test_endpoint_counter_isolates_source_and_recording(counter):
    for source_id, recording in ((0, True), (id(counter), False)):
        core.dispatch_event(SpanCompletedEvent(CompletedSpan(source_id, 1, 1, "web", "ignored", recording)))
    assert endpoints.reset(counter) == ({}, {})


def test_endpoint_stop_restart_has_no_duplicate_subscriptions(tracer):
    for _ in range(2):
        endpoints.enable(tracer)
        endpoints.enable(tracer)
        with tracer.trace("root", resource="a", span_type="web"):
            pass
        assert endpoints.reset(tracer)[0] == {"a": 1}
        endpoints.disable(tracer)
        with tracer.trace("stopped", span_type="web"):
            pass
        assert endpoints.reset(tracer) == ({}, {})


def test_endpoint_registry_does_not_retain_source():
    class Source:
        pass

    source = Source()
    endpoints.enable(source)
    reference = weakref.ref(source)
    counter = endpoints._counters[source]
    del source
    gc.collect()
    assert reference() is None
    assert not counter._enabled


def test_endpoint_child_window_does_not_inherit_parent_counts(counter):
    with counter.trace("parent process", span_type="web"):
        pass
    endpoints._after_fork()
    assert endpoints.reset(counter) == ({}, {})


def test_endpoint_subscription_removed_after_failed_profiler_start(tracer, monkeypatch):
    from ddtrace.profiling.profiler import _ProfilerInstance

    instance = _ProfilerInstance(tracer=tracer, endpoint_collection_enabled=True)
    monkeypatch.setattr(instance, "_arm_native_heap_profiling", lambda: None)
    monkeypatch.setattr(instance, "_start_exporter", lambda: None)

    def fail():
        assert tracer in endpoints._counters
        raise RuntimeError("collector startup failed")

    monkeypatch.setattr(instance, "_start_collectors", fail)
    with pytest.raises(RuntimeError, match="collector startup failed"):
        instance._start_service()
    assert tracer not in endpoints._counters

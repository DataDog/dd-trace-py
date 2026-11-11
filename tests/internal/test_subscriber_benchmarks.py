import pytest


@pytest.mark.subprocess(parametrize={"BUNDLE_SUPPORT": ["present", "absent"]})
def test_subscriber_benchmarks_support_release_baselines() -> None:
    import os
    import sys
    from unittest.mock import patch

    from ddtrace.internal import core
    from ddtrace.internal.core import subscriber

    sys.path.insert(0, "benchmarks")
    with patch("bm._scenario._register"):
        from benchmarks.events_api.scenario import EventsAPIScenario

    if os.environ["BUNDLE_SUPPORT"] == "absent":
        del subscriber.SubscriberBundle

    for api in ("subscriber_setup", "subscriber_registration", "subscriber_reset_recovery"):
        core.reset_listeners("context.started.web.request")
        core.reset_listeners("context.ended.web.request")
        workload = EventsAPIScenario(name=api, api=api).run()
        next(workload)(2)
        assert next(workload, None) is None
        assert core.has_listeners("context.started.web.request")
        assert core.has_listeners("context.ended.web.request")


@pytest.mark.subprocess(
    parametrize={"BENCHMARK_API": ["core", "events", "dispatch", "dispatch_event"], "TEARDOWN": ["exhaust", "close"]}
)
def test_event_benchmarks_do_not_accumulate_listeners() -> None:
    import os
    import sys
    from unittest.mock import patch

    from ddtrace.internal import core
    from ddtrace.trace import tracer

    sys.path.insert(0, "benchmarks")
    with patch("bm._scenario._register"):
        from benchmarks.events_api.scenario import BenchmarkDispatchEvent
        from benchmarks.events_api.scenario import EventsAPIScenario

    api = os.environ["BENCHMARK_API"]
    events = {
        "core": ("context.started.core.api.event", "context.ended.core.api.event"),
        "events": ("context.started.events.api.event", "context.ended.events.api.event"),
        "dispatch": ("core.api.dispatch.event",),
        "dispatch_event": ("events.api.dispatch.event",),
    }[api]
    observed = []
    spans = []
    start_span = tracer.start_span

    def external_listener(*args):
        observed.append(args)

    def capture_span(*args, **kwargs):
        span = start_span(*args, **kwargs)
        spans.append(span)
        return span

    # Cleanup must preserve listeners belonging to other consumers of the event.
    for event in events:
        assert not core.has_listeners(event)
        core.on(event, external_listener)
    scenario = EventsAPIScenario(name=api, api=api)
    with patch.object(tracer, "start_span", side_effect=capture_span):
        for _ in range(3):
            workload = scenario.run()
            measured = next(workload)
            try:
                for loops in (2, 3):
                    spans.clear()
                    observed.clear()
                    measured(loops)
                    assert len(observed) == loops * len(events)
                    if api in ("core", "events"):
                        assert len(spans) == loops
                        assert all(span.finished for span in spans)
                        assert tracer.current_span() is None
                    else:
                        args = (
                            (BenchmarkDispatchEvent(url="myurl.com", method="GET", status_code=200),)
                            if api == "dispatch_event"
                            else ("myurl.com", "GET", 200)
                        )
                        assert len(core.dispatch_with_results(events[0], args)) == 2
            finally:
                if os.environ["TEARDOWN"] == "exhaust":
                    assert next(workload, None) is None
                else:
                    workload.close()
            for event in events:
                assert core.has_listeners(event)
                core.reset_listeners(event, external_listener)
                assert not core.has_listeners(event)
                core.on(event, external_listener)
    for event in events:
        core.reset_listeners(event, external_listener)

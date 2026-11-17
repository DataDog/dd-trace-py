import pytest


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_ray_subscriber_definitions_do_not_register_listeners() -> None:
    from importlib import import_module
    from importlib import reload
    import sys

    from ddtrace._trace._registration import RAY_SUBSCRIBERS
    from ddtrace.internal import core
    from ddtrace.internal.core.subscriber import ContextSubscriber

    assert "ddtrace.contrib.internal.ray.patch" not in sys.modules
    module = import_module(RAY_SUBSCRIBERS.module)
    reload(module)
    for name in RAY_SUBSCRIBERS.subscribers:
        subscriber = getattr(module, name)
        assert subscriber.auto_register is False
        prefixes = ("context.started.", "context.ended.") if issubclass(subscriber, ContextSubscriber) else ("",)
        for event_name in subscriber.event_names:
            for prefix in prefixes:
                assert not core.has_listeners(prefix + event_name), (name, event_name)


@pytest.mark.subprocess(
    parametrize={"DD_TRACE_ENABLED": ["true", "false"], "_DD_GLOBAL_TRACER_INIT": ["true", "false"]}
)
def test_manual_ray_patching_activates_subscribers() -> None:
    from importlib import import_module
    import sys

    from ddtrace import config
    from ddtrace._trace._registration import RAY_SUBSCRIBERS
    from ddtrace.contrib._events.ray import RayContextInjectionEvent
    from ddtrace.contrib._events.ray import RaySubmissionEvent
    from ddtrace.contrib.internal.ray.constants import DD_RAY_TRACE_CTX
    from ddtrace.internal import core
    from ddtrace.internal.core.subscriber import ContextSubscriber
    from ddtrace.internal.span_bus import span_from_context
    from ddtrace.trace import tracer

    assert RAY_SUBSCRIBERS.module not in sys.modules
    patch_module = import_module("ddtrace.contrib.internal.ray.patch")
    module = import_module(RAY_SUBSCRIBERS.module)
    # The tracing-owned hook activates listeners when the integration module loads.
    for name in RAY_SUBSCRIBERS.subscribers:
        subscriber = getattr(module, name)
        prefixes = ("context.started.", "context.ended.") if issubclass(subscriber, ContextSubscriber) else ("",)
        for event_name in subscriber.event_names:
            for prefix in prefixes:
                assert core.has_listeners(prefix + event_name), (name, event_name)

    patch_module.patch()
    patch_module.patch()
    with tracer.trace("parent") as parent:
        injection = RayContextInjectionEvent(kwargs={})
        core.dispatch_event(injection)
        assert injection.current_context is parent.context
        assert DD_RAY_TRACE_CTX in injection.kwargs["kwargs"]
        event = RaySubmissionEvent(
            component="ray", integration_config=config.ray, service="test-svc", is_task_submission=True
        )
        with core.context_with_event(event) as ctx:
            span = span_from_context(ctx)
            assert span.name == "task.submit"
            assert span.parent_id == parent.span_id
            assert not span.finished
        assert span.finished


def test_ray_product_start_restores_listeners_and_unpatch_preserves_completion(tracer, test_spans) -> None:
    from importlib import import_module

    import ray.dashboard.modules.job.job_manager  # noqa: F401

    from ddtrace import config
    from ddtrace._trace import product
    from ddtrace._trace._registration import RAY_SUBSCRIBERS
    from ddtrace.contrib._events.ray import RayContextInjectionEvent
    from ddtrace.contrib._events.ray import RaySubmissionEvent
    from ddtrace.internal import core
    from ddtrace.internal.span_bus import span_from_context

    patch_module = import_module("ddtrace.contrib.internal.ray.patch")
    patch_module.patch()
    RAY_SUBSCRIBERS.unregister()
    assert not core.has_listeners("context.started.ray.submit")
    assert not core.has_listeners(RayContextInjectionEvent.event_name)
    product.start()
    product.start()
    assert core.has_listeners(RayContextInjectionEvent.event_name)
    event = RaySubmissionEvent(
        component="ray", integration_config=config.ray, service="test-svc", is_task_submission=True
    )
    try:
        with core.context_with_event(event) as ctx:
            span = span_from_context(ctx)
            patch_module.unpatch()
            assert not span.finished
        assert span.finished
        assert test_spans.pop() == [span]
    finally:
        patch_module.unpatch()

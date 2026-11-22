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
        prefixes = ("context.started.", "context.ended.") if issubclass(subscriber, ContextSubscriber) else ("",)
        for event_name in subscriber.event_names:
            for prefix in prefixes:
                assert not core.has_listeners(prefix + event_name), (name, event_name)


@pytest.mark.subprocess(env=dict(_DD_GLOBAL_TRACER_INIT="false"))
def test_manual_ray_patching_activates_subscribers_with_deferred_tracer() -> None:
    from importlib import import_module
    import sys

    from ddtrace._trace._registration import RAY_SUBSCRIBERS
    from ddtrace.internal import core
    from ddtrace.internal.core.subscriber import ContextSubscriber

    assert RAY_SUBSCRIBERS.module not in sys.modules
    patch_module = import_module("ddtrace.contrib.internal.ray.patch")
    patch_module.patch()
    module = import_module(RAY_SUBSCRIBERS.module)
    for name in RAY_SUBSCRIBERS.subscribers:
        subscriber = getattr(module, name)
        prefixes = ("context.started.", "context.ended.") if issubclass(subscriber, ContextSubscriber) else ("",)
        for event_name in subscriber.event_names:
            for prefix in prefixes:
                assert core.has_listeners(prefix + event_name), (name, event_name)

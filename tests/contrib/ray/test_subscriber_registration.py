import pytest


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "true"})
def test_tracing_activates_ray_subscribers_after_integration_import():
    from importlib import import_module
    import sys

    from ddtrace.contrib._events.ray import RayEvents
    from ddtrace.internal import core

    assert "ray" not in sys.modules
    event_name = RayEvents.RAY_JOB.value
    assert not core.has_listeners(f"context.started.{event_name}")
    import_module("ddtrace.contrib.internal.ray.patch")
    assert core.has_listeners(f"context.started.{event_name}")
    assert core.has_listeners(f"context.ended.{event_name}")


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "true"})
def test_terminal_tracing_stop_prevents_late_ray_activation():
    from importlib import import_module
    import sys

    from ddtrace._trace import product
    from ddtrace.contrib._events.ray import RayEvents
    from ddtrace.internal import core

    assert "ray" not in sys.modules
    product.stop()
    import_module("ddtrace.contrib.internal.ray.patch")
    event_name = RayEvents.RAY_JOB.value
    assert not core.has_listeners(f"context.started.{event_name}")
    assert not core.has_listeners(f"context.ended.{event_name}")


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "true"})
def test_terminal_stop_while_ray_subscriber_import_is_blocked():
    from concurrent.futures import ThreadPoolExecutor
    from importlib import import_module
    from threading import Event
    from unittest.mock import patch

    from ddtrace._trace import _registration
    from ddtrace._trace import product
    from ddtrace.contrib._events.ray import RayEvents
    from ddtrace.internal import core

    entered = Event()
    resume = Event()

    def blocked_import(name):
        entered.set()
        assert resume.wait(10)
        return import_module(name)

    with patch.object(_registration, "import_module", blocked_import):
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(import_module, "ddtrace.contrib.internal.ray.patch")
            try:
                assert entered.wait(10)
                product.stop()
            finally:
                resume.set()
            pending.result(timeout=10)
    event_name = RayEvents.RAY_JOB.value
    assert not core.has_listeners(f"context.started.{event_name}")
    assert not core.has_listeners(f"context.ended.{event_name}")

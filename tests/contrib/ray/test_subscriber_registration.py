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

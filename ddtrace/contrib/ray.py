import logging

from ray.util.tracing import tracing_helper
from ray.util.tracing.tracing_helper import _is_tracing_enabled


log = logging.getLogger(__name__)


def setup_tracing():
    from ddtrace import patch
    import ddtrace.auto  # noqa:F401

    patch(ray=True)

    if _is_tracing_enabled():
        # Specifying a hook will activate opentelemetry instrumentation automatically.
        # We silently deactivate it as users might be confused as they
        # probably do not activate otel on purpose
        tracing_helper._global_is_tracing_enabled = False


def enable_tracing_before_init(actor=True, task=True):
    if actor:
        from ray import actor

        from ddtrace.contrib.internal.ray.patch import _inject_tracing_into_actor_class

        actor._inject_tracing_into_class = _inject_tracing_into_actor_class

    if task:
        from ray import remote_function

        from ddtrace.contrib.internal.ray.patch import _inject_tracing_into_function

        remote_function._inject_tracing_into_function = _inject_tracing_into_function

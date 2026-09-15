import sys
from typing import Any
from typing import Callable
from typing import Optional

import gevent
from gevent.monkey import get_original
from greenlet import gettrace
from greenlet import settrace

from ddtrace.internal import core
from ddtrace.internal._context_watcher import PYTHON_CONTEXT_SWITCH_EVENT
from ddtrace.internal.settings._config import config
from ddtrace.trace import tracer


_state = get_original("threading", "local")()

# The context-switch watcher only has a consumer on Linux (see
# ddtrace.internal.opentelemetry.thread_context), so avoid the settrace overhead elsewhere.
_CONTEXT_SWITCH_ENABLED = sys.platform == "linux" and config._otel_thread_context_enabled


class _GreenletTrace:
    def __init__(self, previous: Optional[Callable[[str, Any], None]]) -> None:
        self.previous = previous

    def __call__(self, event: str, args: Any) -> None:
        if getattr(gevent, "__datadog_patch", False):
            # A displaced watcher can remain in another callback's chain, so only
            # the current watcher publishes the context switch.
            if getattr(_state, "trace", None) is self:
                core.dispatch(PYTHON_CONTEXT_SWITCH_EVENT)
        elif gettrace() is self:
            settrace(self.previous)
            _state.trace = None

        if self.previous is not None:
            self.previous(event, args)


def ensure_greenlet_context_switch() -> bool:
    """Install the context-switch watcher in the current native thread."""
    if not _CONTEXT_SWITCH_ENABLED or not getattr(gevent, "__datadog_patch", False):
        return False

    trace = getattr(_state, "trace", None)
    current_trace = gettrace()
    if trace is None or current_trace is not trace:
        trace = _GreenletTrace(current_trace)
        settrace(trace)
        _state.trace = trace

    return True


def disable_greenlet_context_switch() -> None:
    trace = getattr(_state, "trace", None)
    if trace is not None and gettrace() is trace:
        settrace(trace.previous)
        _state.trace = None


class TracingMixin(object):
    def __init__(self, *args, **kwargs):
        ensure_greenlet_context_switch()
        # Store the current Datadog context.
        # This is necessary to ensure tracing context is passed to greenlets.
        # Avoids setting Greenlet.gr_context, setting field could introduce
        # unintended side-effects in third party libraries.
        self.trace_context = tracer.context_provider.active()
        super(TracingMixin, self).__init__(*args, **kwargs)

    def run(self):
        # Propagates Datadog context to spawned greenlets
        tracer.context_provider.activate(self.trace_context)
        super(TracingMixin, self).run()


class TracedGreenlet(TracingMixin, gevent.Greenlet):
    """
    ``Greenlet`` class that is used to replace the original ``gevent``
    class. This class ensures any greenlet inherits the contextvars from the parent Greenlet.

    There is no need to inherit this class to create or optimize greenlets
    instances, because this class replaces ``gevent.greenlet.Greenlet``
    through the ``patch()`` method. After the patch, extending the gevent
    ``Greenlet`` class means extending automatically ``TracedGreenlet``.
    """

    def __init__(self, *args, **kwargs):
        super(TracedGreenlet, self).__init__(*args, **kwargs)


class TracedIMapUnordered(TracingMixin, gevent.pool.IMapUnordered):
    def __init__(self, *args, **kwargs):
        super(TracedIMapUnordered, self).__init__(*args, **kwargs)


class TracedIMap(TracedIMapUnordered, gevent.pool.IMap):
    def __init__(self, *args, **kwargs):
        super(TracedIMap, self).__init__(*args, **kwargs)

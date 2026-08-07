from typing import Any

import gevent

from ddtrace._trace.provider import _DD_CONTEXTVAR
from ddtrace.internal import core


GEVENT_VERSION = gevent.version_info[0:3]


def _context_switch_trace(event: str, args: Any) -> None:
    # Greenlet swaps thread-state contexts directly, bypassing CPython's
    # context watcher even on Python 3.14+.
    if event in {"switch", "throw"}:
        core.dispatch("python.context.switch")


class TracingMixin(object):
    def __init__(self, *args, **kwargs):
        # Store the current Datadog context.
        # This is necessary to ensure tracing context is passed to greenlets.
        # Avoid changing gr_context, which may affect third-party libraries.
        self.trace_context = _DD_CONTEXTVAR.get()
        super(TracingMixin, self).__init__(*args, **kwargs)

    def run(self):
        # Propagates Datadog context to spawned greenlets
        _DD_CONTEXTVAR.set(self.trace_context)
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

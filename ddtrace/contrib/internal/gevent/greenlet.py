from typing import Any

import gevent

from ddtrace.internal import core
from ddtrace.trace import tracer


GEVENT_VERSION = gevent.version_info[0:3]


def _context_switch_trace(event: str, args: Any) -> None:
    if event in {"switch", "throw"}:
        core.dispatch(
            "ddtrace.context_provider.activate",
            (tracer.context_provider, tracer.context_provider.active()),
        )


class TracingMixin(object):
    def __init__(self, *args, **kwargs):
        # Avoid changing gr_context, which may affect third-party libraries.
        self.trace_context = tracer.context_provider.active()
        super(TracingMixin, self).__init__(*args, **kwargs)

    def run(self):
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

import gevent
import gevent.pool as gpool

from .provider import GeventContextProvider


GEVENT_VERSION = gevent.version_info[0:3]


class TracingMixin(object):
    def __init__(self, *args, **kwargs):
        # get the active context/span if available
        current_g = gevent.getcurrent()
        ctx = getattr(current_g, GeventContextProvider._CONTEXT_ATTR, None)

        # create the Greenlet as usual
        super(TracingMixin, self).__init__(*args, **kwargs)

        # copy the active span/context into the new greenlet
        if ctx:
            setattr(self, GeventContextProvider._CONTEXT_ATTR, ctx)


class TracedGreenlet(TracingMixin, gevent.Greenlet):
    """
    ``Greenlet`` class that is used to replace the original ``gevent``
    class. This class is supposed to do ``Context`` replacing operation, so
    that any greenlet inherits the context from the parent Greenlet.
    When a new greenlet is spawned from the main greenlet, a new instance
    of ``Context`` is created. The main greenlet is not affected by this behavior.

    There is no need to inherit this class to create or optimize greenlets
    instances, because this class replaces ``gevent.greenlet.Greenlet``
    through the ``patch()`` method. After the patch, extending the gevent
    ``Greenlet`` class means extending automatically ``TracedGreenlet``.
    """

    def __init__(self, *args, **kwargs):
        super(TracedGreenlet, self).__init__(*args, **kwargs)


class TracedIMapUnordered(TracingMixin, gpool.IMapUnordered):
    def __init__(self, *args, **kwargs):
        super(TracedIMapUnordered, self).__init__(*args, **kwargs)


if GEVENT_VERSION >= (1, 3) or GEVENT_VERSION < (1, 1):
    # For gevent <1.1 and >=1.3, IMap is its own class, so we derive
    # from TracingMixin
    class TracedIMap(TracingMixin, gpool.IMap):
        def __init__(self, *args, **kwargs):
            super(TracedIMap, self).__init__(*args, **kwargs)


else:
    # For gevent >=1.1 and <1.3, IMap derives from IMapUnordered, so we derive
    # from TracedIMapUnordered and get tracing that way
    class TracedIMap(gpool.IMap, TracedIMapUnordered):
        def __init__(self, *args, **kwargs):
            super(TracedIMap, self).__init__(*args, **kwargs)

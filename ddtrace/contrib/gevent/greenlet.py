from contextvars import copy_context as _copy_context

import gevent


GEVENT_VERSION = gevent.version_info[0:3]


class TracingMixin(object):
    def __init__(self, *args, **kwargs):
        # Initialize greenlet contextvars to the same as the current context.
        # This is necessary to ensure tracing context is passed to greenlets.
        # Note - this change may impact other libraries that rely on greenlet local storage.
        self.gr_context = _copy_context()
        super(TracingMixin, self).__init__(*args, **kwargs)


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

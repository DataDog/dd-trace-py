from contextvars import copy_context as _copy_context

import gevent


GEVENT_VERSION = gevent.version_info[0:3]


class TracedGreenlet(gevent.Greenlet):
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
        # Initialize greenlet contextvars to the same as the current context.
        # This is necessary to ensure tracing context is passed to greenlets.
        # Note - this change may impact other libraries that rely on greenlet local storage.
        self.gr_context = _copy_context()
        super(TracedGreenlet, self).__init__(*args, **kwargs)

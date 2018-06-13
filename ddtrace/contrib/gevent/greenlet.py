import gevent

from .provider import CONTEXT_ATTR


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
        # get the current Context if available
        current_g = gevent.getcurrent()
        ctx = getattr(current_g, CONTEXT_ATTR, None)

        # create the Greenlet as usual
        super(TracedGreenlet, self).__init__(*args, **kwargs)

        # the context is always available made exception of the main greenlet
        if ctx:
            # create a new context that inherits the current active span
            new_ctx = ctx.clone()
            setattr(self, CONTEXT_ATTR, new_ctx)

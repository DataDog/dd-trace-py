import gevent
import gevent.pool

import ddtrace

from ...provider import DefaultContextProvider
from .greenlet import GEVENT_VERSION
from .greenlet import TracedGreenlet
from .greenlet import TracedIMap
from .greenlet import TracedIMapUnordered
from .provider import GeventContextProvider


__Greenlet = gevent.Greenlet
__IMap = gevent.pool.IMap
__IMapUnordered = gevent.pool.IMapUnordered


def get_version():
    # type: () -> str
    return getattr(gevent, "__version__", "")


def patch():
    """
    Patch the gevent module so that all references to the
    internal ``Greenlet`` class points to the ``DatadogGreenlet``
    class.

    This action ensures that if a user extends the ``Greenlet``
    class, the ``TracedGreenlet`` is used as a parent class.
    """
    if getattr(gevent, "__datadog_patch", False):
        return
    gevent.__datadog_patch = True

    _replace(TracedGreenlet, TracedIMap, TracedIMapUnordered)
    ddtrace.tracer.configure(context_provider=GeventContextProvider())


def unpatch():
    """
    Restore the original ``Greenlet``. This function must be invoked
    before executing application code, otherwise the ``DatadogGreenlet``
    class may be used during initialization.
    """
    if not getattr(gevent, "__datadog_patch", False):
        return
    gevent.__datadog_patch = False

    _replace(__Greenlet, __IMap, __IMapUnordered)
    ddtrace.tracer.configure(context_provider=DefaultContextProvider())


def _replace(g_class, imap_class, imap_unordered_class):
    """
    Utility function that replace the gevent Greenlet class with the given one.
    """
    # replace the original Greenlet classes with the new one
    gevent.greenlet.Greenlet = g_class

    if GEVENT_VERSION >= (1, 3):
        # For gevent >= 1.3.0, IMap and IMapUnordered were pulled out of
        # gevent.pool and into gevent._imap
        gevent._imap.IMap = imap_class
        gevent._imap.IMapUnordered = imap_unordered_class
        gevent.pool.IMap = gevent._imap.IMap
        gevent.pool.IMapUnordered = gevent._imap.IMapUnordered
        gevent.pool.Greenlet = gevent.greenlet.Greenlet
    else:
        # For gevent < 1.3, only patching of gevent.pool classes necessary
        gevent.pool.IMap = imap_class
        gevent.pool.IMapUnordered = imap_unordered_class

    gevent.pool.Group.greenlet_class = g_class

    # replace gevent shortcuts
    gevent.Greenlet = gevent.greenlet.Greenlet
    gevent.spawn = gevent.greenlet.Greenlet.spawn
    gevent.spawn_later = gevent.greenlet.Greenlet.spawn_later

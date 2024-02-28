import gevent
import gevent.pool

import ddtrace

from .greenlet import TracedGreenlet


__Greenlet = gevent.Greenlet


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

    ddtrace.Pin().onto(gevent)
    _replace(TracedGreenlet)


def unpatch():
    """
    Restore the original ``Greenlet``. This function must be invoked
    before executing application code, otherwise the ``DatadogGreenlet``
    class may be used during initialization.
    """
    if not getattr(gevent, "__datadog_patch", False):
        return
    gevent.__datadog_patch = False

    _replace(__Greenlet)


def _replace(g_class):
    """
    Utility function that replace the gevent Greenlet class with the given one.
    """
    # replace the original Greenlet classes with the new one
    gevent.greenlet.Greenlet = g_class
    gevent.pool.Group.greenlet_class = g_class

    # replace gevent shortcuts
    gevent.Greenlet = gevent.greenlet.Greenlet
    gevent.spawn = gevent.greenlet.Greenlet.spawn
    gevent.spawn_later = gevent.greenlet.Greenlet.spawn_later

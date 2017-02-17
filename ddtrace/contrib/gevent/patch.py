import gevent

from .greenlet import TracedGreenlet


__Greenlet = gevent.Greenlet


def patch():
    """
    Patch the gevent module so that all references to the
    internal ``Greenlet`` class points to the ``DatadogGreenlet``
    class.

    This action ensures that if a user extends the ``Greenlet``
    class, the ``TracedGreenlet`` is used as a parent class.
    """
    _replace(TracedGreenlet)


def unpatch():
    """
    Restore the original ``Greenlet``. This function must be invoked
    before executing application code, otherwise the ``DatadogGreenlet``
    class may be used during initialization.
    """
    _replace(__Greenlet)


def _replace(g_class):
    """
    Utility function that replace the gevent Greenlet class with the given one.
    """
    # replace the original Greenlet class with the new one
    gevent.greenlet.Greenlet = g_class

    # replace gevent shortcuts
    gevent.Greenlet = gevent.greenlet.Greenlet
    gevent.spawn = gevent.greenlet.Greenlet.spawn
    gevent.spawn_later = gevent.greenlet.Greenlet.spawn_later

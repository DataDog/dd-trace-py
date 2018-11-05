"""
To trace a request in a ``gevent`` environment, configure the tracer to use the greenlet
context provider, rather than the default one that relies on a thread-local storaging.

This allows the tracer to pick up a transaction exactly where it left off as greenlets
yield the context to another one.

The simplest way to trace a ``gevent`` application is to configure the tracer and
patch ``gevent`` **before importing** the library::

    # patch before importing gevent
    from ddtrace import patch, tracer
    patch(gevent=True)

    # use gevent as usual with or without the monkey module
    from gevent import monkey; monkey.patch_thread()

    def my_parent_function():
        with tracer.trace("web.request") as span:
            span.service = "web"
            gevent.spawn(worker_function)

    def worker_function():
        # then trace its child
        with tracer.trace("greenlet.call") as span:
            span.service = "greenlet"
            ...

            with tracer.trace("greenlet.child_call") as child:
                ...
"""
import ddtrace

from ...provider import DefaultContextProvider
from ...utils.install import install_module_import_hook, mark_module_unpatched

from .provider import GeventContextProvider


__all__ = [
    'context_provider',
    'patch',
    'unpatch',
]


__Greenlet = None
__IMap = None
__IMapUnordered = None

context_provider = GeventContextProvider()


def _patch(gevent):
    """
    Patch the gevent module so that all references to the
    internal ``Greenlet`` class points to the ``DatadogGreenlet``
    class.

    This action ensures that if a user extends the ``Greenlet``
    class, the ``TracedGreenlet`` is used as a parent class.
    """
    # DEV: we must inline these imports to avoid importing gevent when the
    # integration is installed.
    from .greenlet import TracedGreenlet, TracedIMap, TracedIMapUnordered

    global __Greenlet, __IMap, __IMapUnordered
    __Greenlet = gevent.Greenlet
    __IMap = gevent.pool.IMap
    __IMapUnordered = gevent.pool.IMapUnordered
    _replace(gevent, TracedGreenlet, TracedIMap, TracedIMapUnordered)
    ddtrace.tracer.configure(context_provider=GeventContextProvider())


def patch():
    install_module_import_hook('gevent', _patch)


def unpatch():
    """
    Restore the original ``Greenlet``. This function must be invoked
    before executing application code, otherwise the ``DatadogGreenlet``
    class may be used during initialization.
    """
    import gevent
    global __Greenlet, __IMap, __IMapUnordered
    _replace(gevent, __Greenlet, __IMap, __IMapUnordered)
    ddtrace.tracer.configure(context_provider=DefaultContextProvider())
    mark_module_unpatched(gevent)


def _replace(gevent, g_class, imap_class, imap_unordered_class):
    """
    Utility function that replace the gevent Greenlet class with the given one.
    """
    # replace the original Greenlet classes with the new one
    gevent.greenlet.Greenlet = g_class
    gevent.pool.IMap = imap_class
    gevent.pool.IMapUnordered = imap_unordered_class

    gevent.pool.Group.greenlet_class = g_class

    # replace gevent shortcuts
    gevent.Greenlet = gevent.greenlet.Greenlet
    gevent.spawn = gevent.greenlet.Greenlet.spawn
    gevent.spawn_later = gevent.greenlet.Greenlet.spawn_later

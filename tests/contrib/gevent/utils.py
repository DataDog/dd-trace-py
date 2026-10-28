import contextlib
from functools import wraps

import gevent


_NOT_ERROR = gevent.hub.Hub.NOT_ERROR


@contextlib.contextmanager
def gevent_patched(*, force_context_switch=False):
    """Patch gevent for the duration of the block, always unpatching on exit.

    force_context_switch overrides the platform/config gate on the greenlet context-switch
    watcher, so tests can exercise it regardless of the host platform or DD_TRACE_OTEL_CTX_ENABLED.
    """
    import gevent.pool  # noqa:F401  greenlet.py's module-level class defs need this already imported

    from ddtrace.contrib.internal.gevent import greenlet as greenlet_module
    from ddtrace.contrib.internal.gevent.patch import patch
    from ddtrace.contrib.internal.gevent.patch import unpatch

    previous_enabled = greenlet_module._CONTEXT_SWITCH_ENABLED
    if force_context_switch:
        greenlet_module._CONTEXT_SWITCH_ENABLED = True
    patch()
    try:
        yield
    finally:
        unpatch()
        greenlet_module._CONTEXT_SWITCH_ENABLED = previous_enabled


def silence_errors(f):
    """
    Test decorator for gevent that silences all errors when
    a greenlet raises an exception.
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        gevent.hub.Hub.NOT_ERROR = (Exception,)
        f(*args, **kwargs)
        gevent.hub.Hub.NOT_ERROR = _NOT_ERROR

    return wrapper

"""
The ``futures`` integration propagates the current active tracing context
to tasks spawned using a :class:`~concurrent.futures.ThreadPoolExecutor`.
The integration ensures that when operations are executed in another thread,
those operations can continue the previously generated trace.


Enabling
~~~~~~~~

The futures integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch()<ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(futures=True)
"""
from ...internal.utils.importlib import require_modules


required_modules = ["concurrent.futures"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = [
            "get_version",
            "patch",
            "unpatch",
        ]

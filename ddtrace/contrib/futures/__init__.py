"""
The ``futures`` integration propagates the current active tracing context
between threads. The integration ensures that when operations are executed
in a new thread, that thread can continue the previously generated trace.


Enabling
~~~~~~~~

The futures integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(futures=True)
"""
from ...internal.utils.importlib import require_modules


required_modules = ["concurrent.futures"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = [
            "patch",
            "unpatch",
        ]

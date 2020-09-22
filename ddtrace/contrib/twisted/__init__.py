"""
The twisted integration provides context propagation for tracing
across Twisted Deferreds.


Enabling
~~~~~~~~

The integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`patch_all()<patch_all>`.

Or use :ref:`patch()<patch>` to manually enable the integration::

    from ddtrace import patch
    patch(twisted=True)
"""

from ...utils.importlib import require_modules


required_modules = ["twisted"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ["patch", "unpatch"]

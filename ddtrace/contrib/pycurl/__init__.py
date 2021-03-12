"""
The ``pycurl`` integration traces all HTTP calls to internal or external services.
Auto instrumentation is available using the ``patch`` function that **must be called
before** importing the ``pycurl`` library. The following is an example::

    from ddtrace import patch
    patch(pycurl=True)

    import pycurl
    # tktk

:ref:`Headers tracing <http-headers-tracing>` is supported for this integration.
"""
from ...utils.importlib import require_modules


required_modules = ["pycurl"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = [
            "patch",
            "unpatch",
        ]

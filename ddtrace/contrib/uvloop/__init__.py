"""
This integration patches uvloop__ for tracing asynchronous code.

Enable uvloop tracing automatically via ``ddtrace-run``::

    ddtrace-run python app.py

uvloop tracing can also be enabled manually::

    from ddtrace import patch_all
    patch_all(uvloop=True)

.. __: http://uvloop.readthedocs.io/
"""

from ...utils.importlib import require_modules

required_modules = ["uvloop"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch

        __all__ = ["patch", "unpatch"]

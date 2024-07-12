"""
The azure_function integration instruments azure.functions package.


Enabling
~~~~~~~~

Use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(azure_function=True)

"""
from ...internal.utils.importlib import require_modules


required_modules = ["azure.functions"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]

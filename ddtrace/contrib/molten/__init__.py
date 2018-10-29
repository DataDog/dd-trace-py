"""
To trace the molten web framework, install the trace middleware::


To enable distributed tracing when using autopatching, set the
``DATADOG_FALCON_DISTRIBUTED_TRACING`` environment variable to ``True``.
"""
from ...utils.importlib import require_modules

required_modules = ['molten']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ['patch']

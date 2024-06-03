"""
Do later.
"""  # noqa: E501
from ...internal.utils.importlib import require_modules


required_modules = ["anthropic"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from . import patch as _patch

        patch = _patch.patch
        unpatch = _patch.unpatch
        get_version = _patch.get_version

        __all__ = ["patch", "unpatch", "get_version"]

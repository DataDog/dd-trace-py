"""
"""

from ...utils.importlib import require_modules


required_modules = ['multiprocessing']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from . import patch as _patch

        patch = _patch.patch
        unpatch = _patch.unpatch

        __all__ = ['patch', 'unpatch']

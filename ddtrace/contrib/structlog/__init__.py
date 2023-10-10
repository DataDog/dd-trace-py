# patch structlog
# configure with processing, specially JSON
# will automatically inject values

from ...internal.utils.importlib import require_modules


required_modules = ["structlog"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch", "get_version"]

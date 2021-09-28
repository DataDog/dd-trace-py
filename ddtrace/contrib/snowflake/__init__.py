"""
Snowflake
"""
from ...utils.importlib import require_modules


required_modules = ["snowflake.connector"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]

from ...internal.utils.importlib import require_modules


required_modules = ["confluent_kafka"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch

        __all__ = ["patch"]

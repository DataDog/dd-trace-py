from .util import require_modules

required_modules = ['cassandra.cluster']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .session import trace
        __all__ = ['trace']

from ..util import require_modules

required_modules = ['psycopg2']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .connection import connection_factory

        __all__ = ['connection_factory']

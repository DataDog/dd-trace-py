"""
To trace sqlalchemy queries, add instrumentation to the engine class
using the patch method that **must be called before** importing sqlalchemy::

    # patch before importing `create_engine`
    from ddtrace import Pin, patch
    patch(sqlalchemy=True)

    # use SQLAlchemy as usual
    from sqlalchemy import create_engine

    engine = create_engine('sqlite:///:memory:')
    engine.connect().execute("SELECT COUNT(*) FROM users")

    # Use a PIN to specify metadata related to this engine
    Pin.override(engine, service='replica-db')
"""
from ...internal.utils.importlib import require_modules


required_modules = ["sqlalchemy", "sqlalchemy.event"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .engine import trace_engine
        from .patch import patch
        from .patch import unpatch

        __all__ = ["trace_engine", "patch", "unpatch"]

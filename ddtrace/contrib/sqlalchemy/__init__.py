"""
Enabling the SQLAlchemy integration is only necessary if there is no
instrumentation available or enabled for the underlying database engine (e.g.
pymysql, psycopg, mysql-connector, etc.).

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
        # Required to allow users to import from `ddtrace.contrib.sqlalchemy.patch` directly
        from . import patch as _  # noqa: F401, I001

        # Expose public methods
        from ..internal.sqlalchemy.engine import trace_engine
        from ..internal.sqlalchemy.patch import get_version
        from ..internal.sqlalchemy.patch import patch
        from ..internal.sqlalchemy.patch import unpatch

        __all__ = ["trace_engine", "patch", "unpatch", "get_version"]

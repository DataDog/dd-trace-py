"""
Enabling the SQLAlchemy integration is only necessary if there is no
instrumentation available or enabled for the underlying database engine (e.g.
pymysql, psycopg, mysql-connector, etc.).

To trace sqlalchemy queries, add instrumentation to the engine class
using the patch method that **must be called before** importing sqlalchemy::

    # patch before importing `create_engine`
    from ddtrace import patch
    from ddtrace.trace import Pin
    patch(sqlalchemy=True)

    # use SQLAlchemy as usual
    from sqlalchemy import create_engine

    engine = create_engine('sqlite:///:memory:')
    engine.connect().execute("SELECT COUNT(*) FROM users")

    # Use a PIN to specify metadata related to this engine
    Pin.override(engine, service='replica-db')
"""


# Required to allow users to import from  `ddtrace.contrib.sqlalchemy.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.sqlalchemy.engine import trace_engine
from ddtrace.contrib.internal.sqlalchemy.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.sqlalchemy.patch import patch  # noqa: F401
from ddtrace.contrib.internal.sqlalchemy.patch import unpatch  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    if name in ("patch", "unpatch", "get_version"):
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            message="Avoid using this package directly. "
            "Set DD_TRACE_SQLALCHEMY_ENABLED=true and use ``import ddtrace.auto`` or the "
            "``ddtrace-run`` command to enable and configure this integration.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)


__all__ = ["trace_engine"]

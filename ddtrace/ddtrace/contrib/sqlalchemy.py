"""
Enabling the SQLAlchemy integration is only necessary if there is no
instrumentation available or enabled for the underlying database engine (e.g.
pymysql, psycopg, mysql-connector, etc.).

To trace sqlalchemy queries, add instrumentation to the engine class
using the patch method that **must be called before** importing sqlalchemy::

    # patch before importing `create_engine`
    from ddtrace import patch
    patch(sqlalchemy=True)

    # use SQLAlchemy as usual
    from sqlalchemy import create_engine

    engine = create_engine('sqlite:///:memory:')
    engine.connect().execute("SELECT COUNT(*) FROM users")
"""

from ddtrace.contrib.internal.sqlalchemy.engine import trace_engine


__all__ = ["trace_engine"]

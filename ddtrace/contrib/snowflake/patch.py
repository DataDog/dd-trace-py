from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor import wrapt

from ...ext import db
from ...ext import net
from ...utils.formats import asbool
from ...utils.formats import get_env
from ...utils.wrappers import unwrap
from ..dbapi import TracedConnection


config._add(
    "snowflake",
    dict(
        _default_service="snowflake",
        trace_fetch_methods=asbool(get_env("snowflake", "trace_fetch_methods", default=False)),
    ),
)


def patch():
    """Patch monkey patches psycopg's connection function
    so that the connection's functions are traced.
    """
    import snowflake.connector

    if getattr(snowflake.connector, "_datadog_patch", False):
        return
    setattr(snowflake.connector, "_datadog_patch", True)

    wrapt.wrap_function_wrapper("snowflake.connector", "SnowflakeConnection.connect", patched_connect)


def unpatch():
    import snowflake.connector

    if getattr(snowflake.connector, "_datadog_patch", False):
        setattr(snowflake.connector, "_datadog_patch", False)

        unwrap(snowflake.connector.SnowflakeConnection.connet)


def patched_connect(connect_func, _, args, kwargs):
    conn = connect_func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):
    traced_conn = TracedConnection(conn)

    # TODO: Get the real tags we need
    tags = {
        net.TARGET_HOST: conn.host,
        net.TARGET_PORT: conn.port,
        db.NAME: conn.database,
        db.USER: conn.user,
        "db.application": conn.application,
        "db.schema": conn.schema,
        "db.warehouse": conn.warehouse,
    }

    Pin(app="snowflake", tags=tags).onto(traced_conn)

    return traced_conn

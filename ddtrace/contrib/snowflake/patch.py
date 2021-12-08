from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor import wrapt

from ...ext import db
from ...ext import net
from ...internal.utils.formats import asbool
from ...internal.utils.formats import get_env
from ..dbapi import TracedConnection
from ..trace_utils import unwrap


config._add(
    "snowflake",
    dict(
        _default_service="snowflake",
        trace_fetch_methods=asbool(get_env("snowflake", "trace_fetch_methods", default=False)),
    ),
)


def patch():
    import snowflake.connector

    if getattr(snowflake.connector, "_datadog_patch", False):
        return
    setattr(snowflake.connector, "_datadog_patch", True)

    wrapt.wrap_function_wrapper(snowflake.connector, "Connect", patched_connect)
    wrapt.wrap_function_wrapper(snowflake.connector, "connect", patched_connect)


def unpatch():
    import snowflake.connector

    if getattr(snowflake.connector, "_datadog_patch", False):
        setattr(snowflake.connector, "_datadog_patch", False)

        unwrap(snowflake.connector, "Connect")
        unwrap(snowflake.connector, "connect")


def patched_connect(connect_func, _, args, kwargs):
    conn = connect_func(*args, **kwargs)
    if isinstance(conn, TracedConnection):
        return conn

    # Add default tags to each query
    tags = {
        net.TARGET_HOST: conn.host,
        net.TARGET_PORT: conn.port,
        db.NAME: conn.database,
        db.USER: conn.user,
        "db.application": conn.application,
        "db.schema": conn.schema,
        "db.warehouse": conn.warehouse,
    }

    pin = Pin(tags=tags)
    traced_conn = TracedConnection(conn, pin=pin, cfg=config.snowflake)
    pin.onto(traced_conn)
    return traced_conn

import os

from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor import wrapt

from ...ext import db
from ...ext import net
from ...internal.utils.formats import asbool
from ..dbapi import TracedConnection
from ..dbapi import TracedCursor
from ..trace_utils import unwrap


config._add(
    "snowflake",
    dict(
        _default_service="snowflake",
        # FIXME: consistent prefix span names with other dbapi integrations
        # The snowflake integration was introduced following a different pattern
        # than all other dbapi-compliant integrations. It sets span names to
        # `sql.query` whereas other dbapi-compliant integrations are set to
        # `<integration>.query`.
        _dbapi_span_name_prefix="sql",
        trace_fetch_methods=asbool(os.getenv("DD_SNOWFLAKE_TRACE_FETCH_METHODS", default=False)),
    ),
)


class _SFTracedCursor(TracedCursor):
    def _set_post_execute_tags(self, span):
        super(_SFTracedCursor, self)._set_post_execute_tags(span)
        span._set_str_tag("sfqid", self.__wrapped__.sfqid)


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
    traced_conn = TracedConnection(conn, pin=pin, cfg=config.snowflake, cursor_cls=_SFTracedCursor)
    pin.onto(traced_conn)
    return traced_conn

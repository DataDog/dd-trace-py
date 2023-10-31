import os

from ddtrace import Pin
from ddtrace import config
from ddtrace.vendor import wrapt

from ...ext import db
from ...ext import net
from ...internal.schema import schematize_service_name
from ...internal.utils.formats import asbool
from ..dbapi import TracedConnection
from ..dbapi import TracedCursor
from ..trace_utils import unwrap


config._add(
    "snowflake",
    dict(
        _default_service=schematize_service_name("snowflake"),
        # FIXME: consistent prefix span names with other dbapi integrations
        # The snowflake integration was introduced following a different pattern
        # than all other dbapi-compliant integrations. It sets span names to
        # `sql.query` whereas other dbapi-compliant integrations are set to
        # `<integration>.query`.
        _dbapi_span_name_prefix="sql",
        trace_fetch_methods=asbool(os.getenv("DD_SNOWFLAKE_TRACE_FETCH_METHODS", default=False)),
    ),
)


def get_version():
    # type: () -> str
    try:
        import snowflake.connector as c
    except AttributeError:
        import sys

        c = sys.modules.get("snowflake.connector")
    return str(c.__version__)


class _SFTracedCursor(TracedCursor):
    def _set_post_execute_tags(self, span):
        super(_SFTracedCursor, self)._set_post_execute_tags(span)
        span.set_tag_str("sfqid", self.__wrapped__.sfqid)


def patch():
    try:
        import snowflake.connector as c
    except AttributeError:
        import sys

        c = sys.modules.get("snowflake.connector")

    if getattr(c, "_datadog_patch", False):
        return
    c._datadog_patch = True

    wrapt.wrap_function_wrapper(c, "Connect", patched_connect)
    wrapt.wrap_function_wrapper(c, "connect", patched_connect)


def unpatch():
    try:
        import snowflake.connector as c
    except AttributeError:
        import sys

        c = sys.modules.get("snowflake.connector")

    if getattr(c, "_datadog_patch", False):
        c._datadog_patch = False

        unwrap(c, "Connect")
        unwrap(c, "connect")


def patched_connect(connect_func, _, args, kwargs):
    conn = connect_func(*args, **kwargs)
    if isinstance(conn, TracedConnection):
        return conn

    # Add default tags to each query
    tags = {
        net.TARGET_HOST: conn.host,
        net.TARGET_PORT: conn.port,
        db.NAME: conn.database,
        db.SYSTEM: "snowflake",
        db.USER: conn.user,
        "db.application": conn.application,
        "db.schema": conn.schema,
        "db.warehouse": conn.warehouse,
    }

    pin = Pin(tags=tags)
    traced_conn = TracedConnection(conn, pin=pin, cfg=config.snowflake, cursor_cls=_SFTracedCursor)
    pin.onto(traced_conn)
    return traced_conn

import os

import clickhouse_driver
from ddtrace import Pin, config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.vendor import wrapt

from ...ext import db
from ...ext import net
from ...internal.utils.formats import asbool

config._add(
    "clickhouse_driver",
    dict(
        _default_service="clickhouse",
        _dbapi_span_name_prefix="clickhouse",
        trace_fetch_methods=asbool(os.getenv("DD_CLICKHOUSE_TRACE_FETCH_METHODS", default=False)),
    ),
)

CONN_ATTR_BY_TAG = {
    net.TARGET_HOST: "host",
    net.TARGET_PORT: "port",
    db.USER: "user",
    db.NAME: "database",
}


def patch():
    wrapt.wrap_function_wrapper("clickhouse_driver.dbapi", "connect", _connect)
    # `Connect` is an alias for `connect`, patch it too
    if hasattr(clickhouse_driver.dbapi.connection, "Connect"):
        clickhouse_driver.dbapi.connection.Connect = clickhouse_driver.dbapi.connect


def unpatch():
    if isinstance(clickhouse_driver.dbapi.connect, wrapt.ObjectProxy):
        clickhouse_driver.dbapi.connect = clickhouse_driver.dbapi.connect.__wrapped__
        if hasattr(clickhouse_driver.dbapi.connection, "Connect"):
            clickhouse_driver.dbapi.Connect = clickhouse_driver.dbapi.connect


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):

    tags = {t: getattr(conn, a) for t, a in CONN_ATTR_BY_TAG.items() if getattr(conn, a, "") != ""}
    tags[db.SYSTEM] = "clickhouse"
    pin = Pin(tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.clickhouse)
    pin.onto(wrapped)
    return wrapped

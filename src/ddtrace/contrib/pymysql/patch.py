import os

import pymysql

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.vendor import wrapt

from ...ext import db
from ...ext import net
from ...internal.schema import schematize_database_operation
from ...internal.schema import schematize_service_name
from ...internal.utils.formats import asbool


config._add(
    "pymysql",
    dict(
        _default_service=schematize_service_name("pymysql"),
        _dbapi_span_name_prefix="pymysql",
        _dbapi_span_operation_name=schematize_database_operation("pymysql.query", database_provider="mysql"),
        trace_fetch_methods=asbool(os.getenv("DD_PYMYSQL_TRACE_FETCH_METHODS", default=False)),
    ),
)

CONN_ATTR_BY_TAG = {
    net.TARGET_HOST: "host",
    net.TARGET_PORT: "port",
    db.USER: "user",
    db.NAME: "db",
}


def patch():
    wrapt.wrap_function_wrapper("pymysql", "connect", _connect)


def unpatch():
    if isinstance(pymysql.connect, wrapt.ObjectProxy):
        pymysql.connect = pymysql.connect.__wrapped__


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):
    tags = {t: getattr(conn, a, "") for t, a in CONN_ATTR_BY_TAG.items()}
    tags[db.SYSTEM] = "mysql"
    pin = Pin(tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.pymysql)
    pin.onto(wrapped)
    return wrapped

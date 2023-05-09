import os

import mysql.connector

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
    "mysql",
    dict(
        _default_service=schematize_service_name("mysql"),
        _dbapi_span_name_prefix="mysql",
        _dbapi_span_operation_name=schematize_database_operation("mysql.query", database_provider="mysql"),
        trace_fetch_methods=asbool(os.getenv("DD_MYSQL_TRACE_FETCH_METHODS", default=False)),
    ),
)

CONN_ATTR_BY_TAG = {
    net.TARGET_HOST: "server_host",
    net.TARGET_PORT: "server_port",
    db.USER: "user",
    db.NAME: "database",
}


def patch():
    wrapt.wrap_function_wrapper("mysql.connector", "connect", _connect)
    # `Connect` is an alias for `connect`, patch it too
    if hasattr(mysql.connector, "Connect"):
        mysql.connector.Connect = mysql.connector.connect


def unpatch():
    if isinstance(mysql.connector.connect, wrapt.ObjectProxy):
        mysql.connector.connect = mysql.connector.connect.__wrapped__
        if hasattr(mysql.connector, "Connect"):
            mysql.connector.Connect = mysql.connector.connect


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):

    tags = {t: getattr(conn, a) for t, a in CONN_ATTR_BY_TAG.items() if getattr(conn, a, "") != ""}
    tags[db.SYSTEM] = "mysql"
    pin = Pin(tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.mysql)
    pin.onto(wrapped)
    return wrapped

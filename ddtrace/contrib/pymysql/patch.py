# 3p
import pymysql

# project
from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.vendor import wrapt

from ...ext import db
from ...ext import net
from ...utils.formats import asbool
from ...utils.formats import get_env


config._add(
    "pymysql",
    dict(
        # TODO[v1.0] this should be "mysql"
        _default_service="pymysql",
        trace_fetch_methods=asbool(get_env("pymysql", "trace_fetch_methods", default=False)),
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
    pin = Pin(app="pymysql", tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.pymysql)
    pin.onto(wrapped)
    return wrapped

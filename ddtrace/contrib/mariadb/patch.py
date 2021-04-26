import mariadb
from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.vendor import wrapt

from ...ext import db
from ...ext import net


config._add(
    "mariadb",
    dict(
        _default_service="mariadb",
    ),
)

CONN_ATTR_BY_TAG = {
    net.TARGET_HOST: "server_host",
    net.TARGET_PORT: "server_port",
    db.USER: "user",
    db.NAME: "database",
}


def patch():
    wrapt.wrap_function_wrapper("mariadb", "connect", _connect)


def unpatch():
    if isinstance(mariadb.connect, wrapt.ObjectProxy):
        mariadb.connect = mariadb.connect.__wrapped__
        if hasattr(mariadb, "Connect"):
            mariadb.Connect = mariadb.connect


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return patch_conn(conn)


def patch_conn(conn):

    tags = {t: getattr(conn, a) for t, a in CONN_ATTR_BY_TAG.items() if getattr(conn, a, "") != ""}
    pin = Pin(app="mariadb", tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.mariadb)
    pin.onto(wrapped)
    return wrapped

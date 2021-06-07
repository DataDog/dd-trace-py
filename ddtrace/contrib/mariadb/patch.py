import mariadb
from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.vendor import wrapt

from ...ext import db
from ...ext import net

from ...utils.formats import asbool
from ...utils.formats import get_env


config._add(
    "mariadb",
    dict(
        trace_fetch_methods=asbool(get_env("mariadb", "trace_fetch_methods", default=False)),
        _default_service="mariadb",
    ),
)




def patch():
    wrapt.wrap_function_wrapper("mariadb", "connect", _connect)


def unpatch():
    if isinstance(mariadb.connect, wrapt.ObjectProxy):
        mariadb.connect = mariadb.connect.__wrapped__
        if hasattr(mariadb, "Connect"):
            mariadb.Connect = mariadb.connect


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    ##need to pull from args as well at some point
    tags = {net.TARGET_HOST: kwargs["host"],
    net.TARGET_PORT: kwargs["port"],
    db.USER: kwargs["user"],
    db.NAME: kwargs["database"],
        }

    pin = Pin(app="mariadb", tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.mariadb)
    pin.onto(wrapped)
    return wrapped
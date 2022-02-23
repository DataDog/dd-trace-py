import os

import MySQLdb

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...ext import SpanTypes
from ...ext import db
from ...ext import net
from ...internal.utils.formats import asbool
from ...internal.utils.wrappers import unwrap as _u


config._add(
    "mysqldb",
    dict(
        _default_service="mysql",
        _dbapi_span_name_prefix="mysql",
        trace_fetch_methods=asbool(os.getenv("DD_MYSQLDB_TRACE_FETCH_METHODS", default=False)),
        trace_connect=asbool(os.getenv("DD_MYSQLDB_TRACE_CONNECT", default=False)),
    ),
)

KWPOS_BY_TAG = {
    net.TARGET_HOST: ("host", 0),
    db.USER: ("user", 1),
    db.NAME: ("db", 3),
}


def patch():
    # patch only once
    if getattr(MySQLdb, "__datadog_patch", False):
        return
    setattr(MySQLdb, "__datadog_patch", True)

    Pin().onto(MySQLdb)

    # `Connection` and `connect` are aliases for
    # `Connect`; patch them too
    _w("MySQLdb", "Connect", _connect)
    if hasattr(MySQLdb, "Connection"):
        _w("MySQLdb", "Connection", _connect)
    if hasattr(MySQLdb, "connect"):
        _w("MySQLdb", "connect", _connect)


def unpatch():
    if not getattr(MySQLdb, "__datadog_patch", False):
        return
    setattr(MySQLdb, "__datadog_patch", False)

    pin = Pin.get_from(MySQLdb)
    if pin:
        pin.remove_from(MySQLdb)

    # unpatch MySQLdb
    _u(MySQLdb, "Connect")
    if hasattr(MySQLdb, "Connection"):
        _u(MySQLdb, "Connection")
    if hasattr(MySQLdb, "connect"):
        _u(MySQLdb, "connect")


def _connect(func, instance, args, kwargs):
    pin = Pin.get_from(MySQLdb)

    if not pin or not pin.enabled() or not config.mysqldb.trace_connect:
        conn = func(*args, **kwargs)
    else:
        with pin.tracer.trace(
            "MySQLdb.connection.connect", service=ext_service(pin, config.mysqldb), span_type=SpanTypes.SQL
        ) as span:
            span.set_tag(SPAN_MEASURED_KEY)
            conn = func(*args, **kwargs)
    return patch_conn(conn, *args, **kwargs)


def patch_conn(conn, *args, **kwargs):
    tags = {
        t: kwargs[k] if k in kwargs else args[p] for t, (k, p) in KWPOS_BY_TAG.items() if k in kwargs or len(args) > p
    }
    tags[net.TARGET_PORT] = conn.port
    pin = Pin(tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.mysqldb)
    pin.onto(wrapped)
    return wrapped

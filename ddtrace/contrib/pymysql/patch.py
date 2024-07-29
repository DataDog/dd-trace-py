import os

import pymysql

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor import wrapt
from ddtrace.vendor.debtcollector import deprecate

from ...ext import db
from ...ext import net
from ...internal.schema import schematize_database_operation
from ...internal.schema import schematize_service_name
from ...internal.utils.formats import asbool
from ...propagation._database_monitoring import _DBM_Propagator
from ..trace_utils import _convert_to_string


config._add(
    "pymysql",
    dict(
        _default_service=schematize_service_name("pymysql"),
        _dbapi_span_name_prefix="pymysql",
        _dbapi_span_operation_name=schematize_database_operation("pymysql.query", database_provider="mysql"),
        trace_fetch_methods=asbool(os.getenv("DD_PYMYSQL_TRACE_FETCH_METHODS", default=False)),
        _dbm_propagator=_DBM_Propagator(0, "query"),
    ),
)


def _get_version():
    # type: () -> str
    return getattr(pymysql, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


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
    return _patch_conn(conn)


def _patch_conn(conn):
    tags = {t: _convert_to_string(getattr(conn, a)) for t, a in CONN_ATTR_BY_TAG.items() if getattr(conn, a, "") != ""}
    tags[db.SYSTEM] = "mysql"
    pin = Pin(tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.pymysql)
    pin.onto(wrapped)
    return wrapped


def patch_conn(conn):
    deprecate(
        "patch_conn is deprecated",
        message="patch_conn is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _patch_conn(conn)

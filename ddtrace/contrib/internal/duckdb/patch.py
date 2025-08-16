import os
from typing import Dict

import duckdb
import wrapt

from ddtrace import config
from ddtrace.contrib.dbapi import FetchTracedCursor
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.trace import Pin

config._add(
    "duckdb",
    dict(
        trace_fetch_methods=asbool(os.getenv("DD_DUCKDB_TRACE_FETCH_METHODS", default=False)),
        _default_service=schematize_service_name("duckdb"),
        _dbapi_span_name_prefix="duckdb",
    ),
)


def _supported_versions() -> dict[str, str]:
    return {"duckdb": ">=1.3.0"}


def get_version():
    # get the package distribution version here
    return duckdb.__version__


def patch():
    if getattr(duckdb, "_datadog_patch", False):
        return
    duckdb._datadog_patch = True
    wrapt.wrap_function_wrapper("duckdb", "connect", _connect)


def unpatch():
    if getattr(duckdb, "_datadog_patch", False):
        duckdb._datadog_patch = False
        unwrap(duckdb, "connect")


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    
    # DuckDB connection parameters
    # For DuckDB, the first argument is typically the database path
    # or ":memory:" for in-memory databases
    database_path = args[0] if args else kwargs.get("database", ":memory:")
    
    # DuckDB is typically used as an embedded database
    # so we don't have traditional host/port like other databases
    # but we can still set some meaningful tags
    tags = {
        db.NAME: database_path,
        db.SYSTEM: "duckdb",
    }

    pin = Pin(tags=tags)

    # Choose cursor class based on trace_fetch_methods configuration
    cursor_cls = FetchTracedCursor if config.duckdb.trace_fetch_methods else TracedCursor

    wrapped = TracedConnection(conn, pin=pin, cfg=config.duckdb, cursor_cls=cursor_cls)
    pin.onto(wrapped)
    return wrapped 

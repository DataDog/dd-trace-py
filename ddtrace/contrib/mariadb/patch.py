import os

import mariadb

from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.vendor import wrapt


config._add(
    "mariadb",
    dict(
        trace_fetch_methods=asbool(os.getenv("DD_MARIADB_TRACE_FETCH_METHODS", default=False)),
        _default_service="mariadb",
        _dbapi_span_name_prefix="mariadb",
    ),
)


def patch():
    if getattr(mariadb, "_datadog_patch", False):
        return
    setattr(mariadb, "_datadog_patch", True)
    wrapt.wrap_function_wrapper("mariadb", "connect", _connect)


def unpatch():
    if getattr(mariadb, "_datadog_patch", False):
        setattr(mariadb, "_datadog_patch", False)
        unwrap(mariadb, "connect")


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    tags = {
        net.TARGET_HOST: kwargs["host"],
        net.TARGET_PORT: kwargs["port"],
        db.USER: kwargs["user"],
        db.NAME: kwargs["database"],
    }

    pin = Pin(tags=tags)

    wrapped = TracedConnection(conn, pin=pin, cfg=config.mariadb)
    pin.onto(wrapped)
    return wrapped

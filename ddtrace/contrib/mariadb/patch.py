import mariadb
from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.vendor import wrapt
from ddtrace.utils import get_argument_value

from ...ext import db
from ...ext import net

from ...utils.formats import asbool
from ...utils.formats import get_env
from ...utils.wrappers import unwrap


config._add(
    "mariadb",
    dict(
        trace_fetch_methods=asbool(get_env("mariadb", "trace_fetch_methods", default=False)),
        _default_service="mariadb",
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

    pin = Pin(app="mariadb", tags=tags)

    # grab the metadata from the conn
    wrapped = TracedConnection(conn, pin=pin, cfg=config.mariadb)
    pin.onto(wrapped)
    return wrapped

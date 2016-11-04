
# stdlib
import logging

# 3p
import psycopg2
import wrapt

# project
from ddtrace import Pin
from ddtrace.contrib import dbapi
from ddtrace.ext import sql, net, db


log = logging.getLogger(__name__)


def patch():
    """ Patch monkey patches psycopg's connection function
        so that the connection's functions are traced.
    """
    setattr(_connect, 'datadog_patched_func', psycopg2.connect)
    wrapt.wrap_function_wrapper('psycopg2', 'connect', _connect)

def unpatch():
    """ Unpatch will undo any monkeypatching. """
    connect = getattr(_connect, 'datadog_patched_func', None)
    if connect is not None:
        psycopg2.connect = connect

def wrap(conn, service="postgres", tracer=None):
    """ Wrap will patch the instance so that it's queries
        are traced. Optionally set the service name of the
        connection.
    """
    c = dbapi.TracedConnection(conn)

    # fetch tags from the dsn
    dsn = sql.parse_pg_dsn(conn.dsn)
    tags = {
        net.TARGET_HOST: dsn.get("host"),
        net.TARGET_PORT: dsn.get("port"),
        db.NAME: dsn.get("dbname"),
        db.USER: dsn.get("user"),
        "db.application" : dsn.get("application_name"),
    }

    pin = Pin(
        service=service,
        app="postgres",
        tracer=tracer,
        tags=tags)

    pin.onto(c)
    return c

def _connect(connect_func, _, args, kwargs):
    db = connect_func(*args, **kwargs)
    return wrap(db)

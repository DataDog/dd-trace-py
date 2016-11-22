# 3p
import psycopg2
import wrapt

# project
from ddtrace import Pin
from ddtrace.contrib import dbapi
from ddtrace.ext import sql, net, db

# Original connect method
_connect = psycopg2.connect

def patch():
    """ Patch monkey patches psycopg's connection function
        so that the connection's functions are traced.
    """
    wrapt.wrap_function_wrapper(psycopg2, 'connect', patched_connect)
    _patch_extensions() # do this early just in case

def unpatch():
    psycopg2.connect = _connect

def patch_conn(conn):
    """ Wrap will patch the instance so that it's queries are traced."""
    # ensure we've patched extensions (this is idempotent) in
    # case we're only tracing some connections.
    _patch_extensions()

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

    Pin(
        service="postgres",
        app="postgres",
        app_type="db",
        tags=tags).onto(c)

    return c

def _patch_extensions():
    # we must patch extensions all the time (it's pretty harmless) so split
    # from global patching of connections. must be idempotent.
    for _, module, func, wrapper in _extensions:
        if not hasattr(module, func) or isinstance(getattr(module, func), wrapt.ObjectProxy):
            continue
        wrapt.wrap_function_wrapper(module, func, wrapper)

def _unpatch_extensions():
    # we must patch extensions all the time (it's pretty harmless) so split
    # from global patching of connections. must be idempotent.
    for original, module, func, _ in _extensions:
        setattr(module, func, original)

#
# monkeypatch targets
#

def patched_connect(connect_func, _, args, kwargs):
    conn = connect_func(*args, **kwargs)
    return patch_conn(conn)

def _extensions_register_type(func, _, args, kwargs):
    def _unroll_args(obj, scope=None):
        return obj, scope
    obj, scope = _unroll_args(*args, **kwargs)

    # register_type performs a c-level check of the object
    # type so we must be sure to pass in the actual db connection
    if scope and isinstance(scope, wrapt.ObjectProxy):
        scope = scope.__wrapped__

    return func(obj, scope) if scope else func(obj)


# extension hooks
_extensions = [
    (psycopg2.extensions.register_type,
     psycopg2.extensions, 'register_type',
     _extensions_register_type),
]

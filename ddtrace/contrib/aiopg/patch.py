import asyncio

# 3p
import aiopg.connection
import aiopg.pool
import psycopg2.extensions
from ddtrace.vendor import wrapt

# ddtrace
from .connection import AIOTracedConnection
from ..psycopg.patch import _patch_extensions, \
    _unpatch_extensions
from ...utils.wrappers import unwrap as _u
from ddtrace.ext import sql, net, db
from ddtrace import Pin


def _create_pin(tags):
    # Will propagate info from global pin
    pin = Pin.get_from(aiopg)
    service = pin.service if pin and pin.service else "postgres_%s" % tags[db.NAME]
    app = pin.app if pin and pin.app else "postgres"
    app_type = pin.app_type if pin and pin.app_type else "db"
    tracer = pin.tracer if pin else None

    if pin and pin.tags:
        # when we drop 3.4 we can switch to: {**tags, **pin.tags}
        tags = dict(tags)
        tags.update(pin.tags)

    return Pin(service=service, app=app, app_type=app_type, tags=tags,
               tracer=tracer)


def _make_dsn(dsn, **kwargs):
    try:
        parsed_dsn = psycopg2.extensions.make_dsn(dsn, **kwargs)

        # fetch tags from the dsn
        parsed_dsn = sql.parse_pg_dsn(parsed_dsn)
        return parsed_dsn
    except ImportError:
        # pre make_dsn you could only have a dsn or kwargs
        # https://github.com/psycopg/psycopg2/commit/1c4523f0ac685632381a0f4371e93031928326b1
        return sql.parse_pg_dsn(dsn) if dsn else kwargs


@asyncio.coroutine
def _patched_connect(connect_func, _, args, kwargs_param):
    @asyncio.coroutine
    def unwrap(dsn=None, *, timeout=aiopg.connection.TIMEOUT, loop=None,  # noqa: E999
               enable_json=True, enable_hstore=True, enable_uuid=True,
               echo=False, **kwargs):

        parsed_dsn = _make_dsn(dsn, **kwargs)

        tags = {
            net.TARGET_HOST: parsed_dsn.get("host"),
            net.TARGET_PORT: parsed_dsn.get("port"),
            db.NAME: parsed_dsn.get("dbname"),
            db.USER: parsed_dsn.get("user"),
            "db.application": parsed_dsn.get("application_name"),
        }

        pin = _create_pin(tags)

        if pin.enabled():
            name = (pin.app or 'sql') + ".connect"
            with pin.tracer.trace(name, service=pin.service) as s:
                s.span_type = sql.TYPE
                s.set_tags(pin.tags)
                conn = yield from connect_func(
                    dsn, timeout=timeout, loop=loop, enable_json=enable_json,
                    enable_hstore=enable_hstore, enable_uuid=enable_uuid,
                    echo=echo, **kwargs)

            conn = AIOTracedConnection(conn, pin)
        else:
            conn = yield from connect_func(
                dsn, timeout=timeout, loop=loop, enable_json=enable_json,
                enable_hstore=enable_hstore, enable_uuid=enable_uuid, echo=echo,
                **kwargs)

        return conn

    result = yield from unwrap(*args, **kwargs_param)
    return result


def _extensions_register_type(func, _, args, kwargs):
    def _unroll_args(obj, scope=None):
        return obj, scope

    obj, scope = _unroll_args(*args, **kwargs)

    # register_type performs a c-level check of the object
    # type so we must be sure to pass in the actual db connection
    if scope and isinstance(scope, wrapt.ObjectProxy):
        scope = scope.__wrapped__._conn

    return func(obj, scope) if scope else func(obj)


# extension hooks
_aiopg_extensions = [
    (psycopg2.extensions.register_type,
     psycopg2.extensions, 'register_type',
     _extensions_register_type),
]


@asyncio.coroutine
def _patched_acquire(acquire_func, instance, args, kwargs):
    parsed_dsn = _make_dsn(instance._dsn, **instance._conn_kwargs)

    tags = {
        net.TARGET_HOST: parsed_dsn.get("host"),
        net.TARGET_PORT: parsed_dsn.get("port"),
        db.NAME: parsed_dsn.get("dbname"),
        db.USER: parsed_dsn.get("user"),
        "db.application": parsed_dsn.get("application_name"),
    }

    pin = _create_pin(tags)

    if not pin.tracer.enabled:
        conn = yield from acquire_func(*args, **kwargs)
        return conn

    with pin.tracer.trace((pin.app or 'sql') + '.pool.acquire',
                          service=pin.service) as s:
        s.span_type = sql.TYPE
        s.set_tags(pin.tags)

        conn = yield from acquire_func(*args, **kwargs)

    return conn


def _patched_release(release_func, instance, args, kwargs):
    parsed_dsn = _make_dsn(instance._dsn, **instance._conn_kwargs)

    tags = {
        net.TARGET_HOST: parsed_dsn.get("host"),
        net.TARGET_PORT: parsed_dsn.get("port"),
        db.NAME: parsed_dsn.get("dbname"),
        db.USER: parsed_dsn.get("user"),
        "db.application": parsed_dsn.get("application_name"),
    }

    pin = _create_pin(tags)

    if not pin.tracer.enabled:
        conn = release_func(*args, **kwargs)
        return conn

    with pin.tracer.trace((pin.app or 'sql') + '.pool.release',
                          service=pin.service) as s:
        s.span_type = sql.TYPE
        s.set_tags(pin.tags)

        conn = release_func(*args, **kwargs)

    return conn


def patch():
    """ Patch monkey patches psycopg's connection function
        so that the connection's functions are traced.
    """
    if getattr(aiopg, '_datadog_patch', False):
        return

    setattr(aiopg, '_datadog_patch', True)

    wrapt.wrap_function_wrapper(aiopg.connection, '_connect', _patched_connect)

    # tracing acquire since it may block waiting for a connection from the pool
    wrapt.wrap_function_wrapper(aiopg.pool.Pool, '_acquire', _patched_acquire)

    # tracing release to match acquire
    wrapt.wrap_function_wrapper(aiopg.pool.Pool, 'release', _patched_release)

    _patch_extensions(_aiopg_extensions)  # do this early just in case


def unpatch():
    if getattr(aiopg, '_datadog_patch', False):
        setattr(aiopg, '_datadog_patch', False)
        _u(aiopg.connection, '_connect')
        _u(aiopg.pool.Pool, '_acquire')
        _u(aiopg.pool.Pool, 'release')
        _unpatch_extensions(_aiopg_extensions)

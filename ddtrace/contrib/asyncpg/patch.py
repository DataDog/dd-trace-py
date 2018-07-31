import asyncio
import functools

# 3p
from asyncpg.protocol import Protocol as orig_Protocol
import asyncpg.protocol
import asyncpg.connect_utils
import asyncpg.pool
import wrapt

# project
from ddtrace.ext import net, db
from ddtrace.pin import Pin
from .connection import AIOTracedProtocol
from ...util import unwrap as _u
from ...ext import sql


def _create_pin(tags):
    # Will propagate info from global pin
    pin = Pin.get_from(asyncpg)
    service = pin.service if pin and pin.service else "postgres_%s", (tags[db.NAME],)
    app = pin.app if pin and pin.app else "postgres"
    app_type = pin.app_type if pin and pin.app_type else "db"
    tracer = pin.tracer if pin else None

    if pin and pin.tags:
        tags = {**tags, **pin.tags}  # noqa: E999

    return Pin(service=service, app=app, app_type=app_type, tags=tags,
               tracer=tracer)


def protocol_factory(protocol_cls, *args, **kwargs):
    def unwrapped(addr, connected_fut, con_params, loop):
        proto = protocol_cls(addr, connected_fut, con_params, loop)  # noqa: E999

        tags = {
            net.TARGET_HOST: addr[0],
            net.TARGET_PORT: addr[1],
            db.NAME: con_params.database,
            db.USER: con_params.user,
            # "db.application" : dsn.get("application_name"),
        }

        pin = _create_pin(tags)

        if not pin.tracer.enabled:
            return proto

        return AIOTracedProtocol(proto, pin)

    return unwrapped


@asyncio.coroutine
def _patched_connect(connect_func, _, args, kwargs):
    tags = {
        net.TARGET_HOST: kwargs['addr'][0],
        net.TARGET_PORT: kwargs['addr'][1],
        db.NAME: kwargs['params'].database,
        db.USER: kwargs['params'].user,
        # "db.application" : dsn.get("application_name"),
    }

    pin = _create_pin(tags)

    if not pin.tracer.enabled:
        conn = yield from connect_func(*args, **kwargs)
        return conn

    with pin.tracer.trace((pin.app or 'sql') + '.connect',
                          service=pin.service) as s:
        s.span_type = sql.TYPE
        s.set_tags(pin.tags)

        conn = yield from connect_func(*args, **kwargs)

    # NOTE: we can't pin anything to the connection object as it's closed
    return conn


@asyncio.coroutine
def _patched_acquire(acquire_func, instance, args, kwargs):
    tags = {
        net.TARGET_HOST: instance._working_addr[0],
        net.TARGET_PORT: instance._working_addr[1],
        db.NAME: instance._working_params.database,
        db.USER: instance._working_params.user,
        # "db.application" : dsn.get("application_name"),
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


@asyncio.coroutine
def _patched_release(release_func, instance, args, kwargs):
    tags = {
        net.TARGET_HOST: instance._working_addr[0],
        net.TARGET_PORT: instance._working_addr[1],
        db.NAME: instance._working_params.database,
        db.USER: instance._working_params.user,
        # "db.application" : dsn.get("application_name"),
    }

    pin = _create_pin(tags)

    if not pin.tracer.enabled:
        conn = yield from release_func(*args, **kwargs)
        return conn

    with pin.tracer.trace((pin.app or 'sql') + '.pool.release',
                          service=pin.service) as s:
        s.span_type = sql.TYPE
        s.set_tags(pin.tags)

        conn = yield from release_func(*args, **kwargs)

    return conn


def patch():
    """ Patch monkey patches various items in asyncpg so that the requests
    will be traced
    """
    if getattr(asyncpg, '_datadog_patch', False):
        return
    setattr(asyncpg, '_datadog_patch', True)

    wrapt.wrap_object(asyncpg.protocol, 'Protocol', protocol_factory)

    # we use _connect_addr to avoid having to parse the dsn and it gets called
    # once per address (there can be multiple)
    wrapt.wrap_function_wrapper(asyncpg.connect_utils,
                                '_connect_addr', _patched_connect)

    # tracing acquire since it may block waiting for a connection from the pool
    wrapt.wrap_function_wrapper(asyncpg.pool.Pool, '_acquire', _patched_acquire)

    # tracing release to match acquire
    wrapt.wrap_function_wrapper(asyncpg.pool.Pool, 'release', _patched_release)


def unpatch():
    if getattr(asyncpg, '_datadog_patch', False):
        setattr(asyncpg, '_datadog_patch', False)
        _u(asyncpg.connect_utils, '_connect_addr')
        _u(asyncpg.pool.Pool, '_acquire')
        _u(asyncpg.pool.Pool, 'release')

        # we can't use unwrap because wrapt does a simple attribute replacement
        asyncpg.protocol.Protocol = orig_Protocol

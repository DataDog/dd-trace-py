import asyncio
import inspect
import warnings

# 3p
from asyncpg.protocol import Protocol as orig_Protocol
import asyncpg.protocol
import asyncpg.connect_utils
import asyncpg.pool

# project
from ddtrace.vendor import wrapt
from ddtrace.ext import net, db
from ddtrace.pin import Pin
from .connection import AIOTracedProtocol
from ...utils.wrappers import unwrap as _u
from ...ext import sql


def _create_pin(tags):
    # Will propagate info from global pin
    pin = Pin.get_from(asyncpg)
    db_name = tags.get(db.NAME)
    service = pin.service if pin and pin.service else 'postgres_%s' % db_name \
        if db_name else 'postgres'
    app = pin.app if pin and pin.app else 'postgres'
    app_type = pin.app_type if pin and pin.app_type else 'db'
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
    assert connect_func != _patched_connect

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


_connect_args = inspect.signature(asyncpg.connection.connect).parameters
_parse_connect_dsn_and_args_params = \
    inspect.signature(
        asyncpg.connect_utils._parse_connect_dsn_and_args).parameters

_connect_parse_arg_mapping = {
    'connect_timeout': 'timeout'
}


def _get_parsed_tags(**connect_kwargs):
    parse_args = dict()

    for param_name, param in _parse_connect_dsn_and_args_params.items():
        # Grab param from connect_kwargs
        connect_param_name = _connect_parse_arg_mapping.get(
            param_name, param_name)
        default = _connect_args[connect_param_name].default
        parse_args[param_name] = connect_kwargs.get(param_name, default)

    try:
        addrs, params, *_ = asyncpg.connect_utils._parse_connect_dsn_and_args(
            **parse_args)

        tags = {
            net.TARGET_HOST: addrs[0][0],
            net.TARGET_PORT: addrs[0][1],
            db.NAME: params.database,
            db.USER: params.user,
        }
        return tags
    except Exception:
        # This same exception will presumably be raised during the actual conn
        return {}


@asyncio.coroutine
def _patched_acquire(acquire_func, instance, args, kwargs):
    if instance._working_addr:
        tags = {
            net.TARGET_HOST: instance._working_addr[0],
            net.TARGET_PORT: instance._working_addr[1],
            db.NAME: instance._working_params.database,
            db.USER: instance._working_params.user,
            # "db.application" : dsn.get("application_name"),
        }
    else:
        conn = instance._queue._queue[0]
        if hasattr(conn, '_connect_kwargs'):
            kwargs_copy = dict(conn._connect_kwargs)
            connect_args = conn._connect_args
        else:
            # this later got moved to the instance
            kwargs_copy = dict(instance._connect_kwargs)
            connect_args = instance._connect_args

        tags = {}
        if len(connect_args) == 1:
            kwargs_copy['dsn'] = connect_args[0]
            tags = _get_parsed_tags(**kwargs_copy)
        else:
            warnings.warn('Unrecognized parameters to asyncpg connect')

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

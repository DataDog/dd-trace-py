import asyncio
import functools

# 3p
from asyncpg.protocol import Protocol as orig_Protocol
import asyncpg.protocol
import asyncpg.connect_utils
import wrapt

# project
from ddtrace.ext import net, db
from ddtrace.pin import Pin
from .connection import AIOTracedProtocol
from ...util import unwrap as _u
from ...ext import sql


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

        pin = Pin(
            service=kwargs['service'],
            app="postgres",
            app_type="db",
            tags=tags,
            tracer=kwargs['tracer'])

        if not pin.tracer.enabled:
            return proto

        return AIOTracedProtocol(proto, pin)

    return unwrapped


@asyncio.coroutine
def _patched_connect(connect_func, _, args, kwargs, tracer, service):
    tags = {
        net.TARGET_HOST: kwargs['host'],
        net.TARGET_PORT: kwargs['port'],
        db.NAME: kwargs['database'],
        db.USER: kwargs['user'],
        # "db.application" : dsn.get("application_name"),
    }

    pin = Pin(
        service=service,
        app="postgres",
        app_type="db",
        tags=tags,
        tracer=tracer)

    if not pin.tracer.enabled:
        conn = yield from connect_func(*args, **kwargs)
        return conn

    with pin.tracer.trace((pin.app or 'sql') + '.connect', service=service) as s:
        s.span_type = sql.TYPE
        s.set_tags(pin.tags)

        conn = yield from connect_func(*args, **kwargs)

    return conn


def patch(service="postgres", tracer=None):
    """ Patch monkey patches asyncpg's Protocol class
        so that the requests are traced
    """
    if getattr(asyncpg, '_datadog_patch', False):
        return
    setattr(asyncpg, '_datadog_patch', True)

    wrapt.wrap_object(asyncpg.protocol, 'Protocol', protocol_factory, kwargs=dict(service=service, tracer=tracer))

    conn_wrap = functools.partial(_patched_connect, tracer=tracer, service=service)
    wrapt.wrap_function_wrapper(asyncpg.connect_utils, '_connect', conn_wrap)


def unpatch():
    if getattr(asyncpg, '_datadog_patch', False):
        setattr(asyncpg, '_datadog_patch', False)
        _u(asyncpg.connect_utils, '_connect')

        # we can't use unwrap because wrapt does a simple attribute replacement
        asyncpg.protocol.Protocol = orig_Protocol

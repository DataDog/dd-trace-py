# 3p
from asyncpg.protocol import Protocol as orig_Protocol
import asyncpg.protocol
import wrapt

# project
from ddtrace.ext import net, db
from ddtrace.pin import Pin
from .connection import AIOTracedProtocol


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

        return AIOTracedProtocol(proto, pin)

    return unwrapped


def patch(service="postgres", tracer=None):
    """ Patch monkey patches asyncpg's Protocol class
        so that the requests are traced
    """
    if getattr(asyncpg, '_datadog_patch', False):
        return
    setattr(asyncpg, '_datadog_patch', True)

    wrapt.wrap_object(asyncpg.protocol, 'Protocol', protocol_factory, kwargs=dict(service=service, tracer=tracer))


def unpatch():
    if getattr(asyncpg, '_datadog_patch', False):
        setattr(asyncpg, '_datadog_patch', False)
        # we can't use unwrap because wrapt does a simple attribute replacement
        asyncpg.protocol.Protocol = orig_Protocol


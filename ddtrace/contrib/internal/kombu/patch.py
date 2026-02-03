import os
from typing import Dict

# 3p
import kombu
import wrapt

from ddtrace import config

# project
from ddtrace.contrib import trace_utils
from ddtrace.contrib.events.kombu import KombuMessagingPublishEvent
from ddtrace.contrib.events.kombu import KombuMessagingReceiveEvent
from ddtrace.ext import kombu as kombux
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.trace import tracer

from .constants import DEFAULT_SERVICE
from .utils import HEADER_POS
from .utils import extract_conn_tags
from .utils import get_body_length_from_args
from .utils import get_exchange_from_args
from .utils import get_routing_key_from_args


def get_version():
    # type: () -> str
    return str(kombu.__version__)


# kombu default settings

config._add(
    "kombu",
    {
        "_default_service": schematize_service_name(DEFAULT_SERVICE),
        "distributed_tracing_enabled": asbool(os.getenv("DD_KOMBU_DISTRIBUTED_TRACING", default=True)),
    },
)


def _supported_versions() -> Dict[str, str]:
    return {"kombu": ">=4.6.6"}


def patch():
    """Patch the instrumented methods

    This duplicated doesn't look nice. The nicer alternative is to use an ObjectProxy on top
    of Kombu. However, it means that any "import kombu.Connection" won't be instrumented.
    """
    if getattr(kombu, "_datadog_patch", False):
        return
    kombu._datadog_patch = True

    _w = wrapt.wrap_function_wrapper
    # We wrap the _publish method because the publish method:
    # *  defines defaults in its kwargs
    # *  potentially overrides kwargs with values from self
    # *  extracts/normalizes things like exchange
    _w("kombu", "Producer._publish", traced_publish)
    _w("kombu", "Consumer.receive", traced_receive)


def unpatch():
    if getattr(kombu, "_datadog_patch", False):
        kombu._datadog_patch = False
        unwrap(kombu.Producer, "_publish")
        unwrap(kombu.Consumer, "receive")


#
# tracing functions
#


def traced_receive(func, instance, args, kwargs):
    # Signature only takes 2 args: (body, message)
    message = get_argument_value(args, kwargs, 1, "message")

    trace_utils.activate_distributed_headers(tracer, request_headers=message.headers, int_config=config.kombu)

    exchange = message.delivery_info["exchange"]
    routing_key = message.delivery_info["routing_key"]
    conn_tags = extract_conn_tags(message.channel.connection)

    with core.context_with_event(
        KombuMessagingReceiveEvent(
            config=config.kombu,
            operation=kombux.RECEIVE_NAME,
            provider="kombu",
            exchange=exchange,
            routing_key=routing_key,
            connection_tags=conn_tags,
        ),
        service=None if config.service else trace_utils.int_service(None, config.kombu),
    ) as ctx:
        result = func(*args, **kwargs)
        core.dispatch("kombu.amqp.receive.post", (instance, message, ctx.span))
        return result


def traced_publish(func, instance, args, kwargs):
    exchange_name = get_exchange_from_args(args)
    routing_key = get_routing_key_from_args(args)
    body_length = get_body_length_from_args(args)
    headers = args[HEADER_POS]
    conn_tags = extract_conn_tags(instance.channel.connection)
    with core.context_with_event(
        KombuMessagingPublishEvent(
            config=config.kombu,
            operation=kombux.PUBLISH_NAME,
            provider="kombu",
            exchange=exchange_name,
            routing_key=routing_key,
            body_length=body_length,
            connection_tags=conn_tags,
            headers=headers,
        ),
        service=trace_utils.ext_service(None, config.kombu) if not config.service else None,
    ) as ctx:
        core.dispatch("kombu.amqp.publish.pre", (args, kwargs, ctx.span))
        return func(*args, **kwargs)

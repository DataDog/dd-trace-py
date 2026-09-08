# 3p
import kombu
import wrapt

from ddtrace import config
from ddtrace._trace.pin import Pin

# project
from ddtrace.contrib._events.messaging import MessagingProcessEvent
from ddtrace.contrib._events.messaging import MessagingProducerEvent
from ddtrace.ext import kombu as kombux
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings import env
from ddtrace.internal.span_bus import span_from_context
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.propagation.http import HTTPPropagator

from .constants import DEFAULT_SERVICE
from .utils import HEADER_POS
from .utils import extract_conn_tags
from .utils import get_body_length_from_args
from .utils import get_exchange_from_args
from .utils import get_routing_key_from_args


def get_version() -> str:
    return str(kombu.__version__)


# kombu default settings

config._add(
    "kombu",
    {
        "distributed_tracing_enabled": asbool(env.get("DD_KOMBU_DISTRIBUTED_TRACING", default=True)),
        "service_name": config.service or env.get("DD_KOMBU_SERVICE", default=DEFAULT_SERVICE),
    },
)

propagator = HTTPPropagator


def _supported_versions() -> dict[str, str]:
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

    # We do not provide a service for producer spans since they represent
    # external calls to another service.
    # Instead the service should be inherited from the parent.
    if config.service:
        prod_service = None
    # DEV: backwards-compatibility for users who set a kombu service
    else:
        prod_service = config.kombu.service or DEFAULT_SERVICE

    Pin(
        service=schematize_service_name(prod_service),
    ).onto(kombu.messaging.Producer)

    Pin(service=schematize_service_name(config.kombu.service or config.kombu["service_name"])).onto(
        kombu.messaging.Consumer
    )


def unpatch():
    if getattr(kombu, "_datadog_patch", False):
        kombu._datadog_patch = False
        unwrap(kombu.Producer, "_publish")
        unwrap(kombu.Consumer, "receive")


#
# tracing functions
#


def traced_receive(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # Signature only takes 2 args: (body, message)
    message = get_argument_value(args, kwargs, 1, "message")
    exchange = message.delivery_info["exchange"]

    event = MessagingProcessEvent(
        operation=schematize_messaging_operation(
            kombux.RECEIVE_NAME, provider="kombu", direction=SpanDirection.PROCESSING
        ),
        request_headers=message.headers,
        component=config.kombu.integration_name,
        integration_config=config.kombu,
        service=pin.service,
        resource=exchange,
    )

    with core.context_with_event(event) as ctx:
        span = span_from_context(ctx)
        span._set_attribute(kombux.EXCHANGE, exchange)
        span.set_tags(extract_conn_tags(message.channel.connection))
        span._set_attribute(kombux.ROUTING_KEY, message.delivery_info["routing_key"])
        result = func(*args, **kwargs)
        core.dispatch("kombu.amqp.receive.post", (instance, message, span))
        return result


def traced_publish(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    exchange_name = get_exchange_from_args(args)
    event = MessagingProducerEvent(
        operation=schematize_messaging_operation(
            kombux.PUBLISH_NAME, provider="kombu", direction=SpanDirection.OUTBOUND
        ),
        distributed_headers=None,
        component=config.kombu.integration_name,
        integration_config=config.kombu,
        service=pin.service,
        resource=exchange_name,
    )

    with core.context_with_event(event) as ctx:
        span = span_from_context(ctx)
        span._set_attribute(kombux.EXCHANGE, exchange_name)
        if pin.tags:
            span.set_tags(pin.tags)
        span._set_attribute(kombux.ROUTING_KEY, get_routing_key_from_args(args))
        span.set_tags(extract_conn_tags(instance.channel.connection))
        span._set_attribute(kombux.BODY_LEN, get_body_length_from_args(args))

        if config.kombu.distributed_tracing_enabled:
            propagator.inject(span.context, args[HEADER_POS])
        core.dispatch(
            "kombu.amqp.publish.pre", (args, kwargs, span)
        )  # Has to happen after trace injection for actual payload size
        return func(*args, **kwargs)

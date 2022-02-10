import os

# 3p
import kombu

from ddtrace import config
from ddtrace.vendor import wrapt

# project
from .. import trace_utils
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import kombu as kombux
from ...internal.utils import get_argument_value
from ...internal.utils.wrappers import unwrap
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from .constants import DEFAULT_SERVICE
from .utils import HEADER_POS
from .utils import extract_conn_tags
from .utils import get_body_length_from_args
from .utils import get_exchange_from_args
from .utils import get_routing_key_from_args


# kombu default settings

config._add(
    "kombu",
    {
        "service_name": config.service or os.getenv("DD_KOMBU_SERVICE_NAME", default=DEFAULT_SERVICE),
    },
)

propagator = HTTPPropagator


def patch():
    """Patch the instrumented methods

    This duplicated doesn't look nice. The nicer alternative is to use an ObjectProxy on top
    of Kombu. However, it means that any "import kombu.Connection" won't be instrumented.
    """
    if getattr(kombu, "_datadog_patch", False):
        return
    setattr(kombu, "_datadog_patch", True)

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
        prod_service = os.getenv("DD_KOMBU_SERVICE_NAME", default=DEFAULT_SERVICE)

    Pin(
        service=prod_service,
    ).onto(kombu.messaging.Producer)

    Pin(service=config.kombu["service_name"]).onto(kombu.messaging.Consumer)


def unpatch():
    if getattr(kombu, "_datadog_patch", False):
        setattr(kombu, "_datadog_patch", False)
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

    trace_utils.activate_distributed_headers(pin.tracer, request_headers=message.headers, override=True)

    with pin.tracer.trace(kombux.RECEIVE_NAME, service=pin.service, span_type=SpanTypes.WORKER) as s:
        s.set_tag(SPAN_MEASURED_KEY)
        # run the command
        exchange = message.delivery_info["exchange"]
        s.resource = exchange
        s.set_tag(kombux.EXCHANGE, exchange)

        s.set_tags(extract_conn_tags(message.channel.connection))
        s.set_tag(kombux.ROUTING_KEY, message.delivery_info["routing_key"])
        # set analytics sample rate
        s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.kombu.get_analytics_sample_rate())
        return func(*args, **kwargs)


def traced_publish(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with pin.tracer.trace(kombux.PUBLISH_NAME, service=pin.service, span_type=SpanTypes.WORKER) as s:
        s.set_tag(SPAN_MEASURED_KEY)
        exchange_name = get_exchange_from_args(args)
        s.resource = exchange_name
        s.set_tag(kombux.EXCHANGE, exchange_name)
        if pin.tags:
            s.set_tags(pin.tags)
        s.set_tag(kombux.ROUTING_KEY, get_routing_key_from_args(args))
        s.set_tags(extract_conn_tags(instance.channel.connection))
        s.set_metric(kombux.BODY_LEN, get_body_length_from_args(args))
        # set analytics sample rate
        s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.kombu.get_analytics_sample_rate())
        # run the command
        propagator.inject(s.context, args[HEADER_POS])
        return func(*args, **kwargs)

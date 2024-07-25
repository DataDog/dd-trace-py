import os

# 3p
import kombu

from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.formats import asbool
from ddtrace.vendor import wrapt
from ddtrace.vendor.debtcollector import deprecate

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_KIND
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanKind
from ...ext import SpanTypes
from ...ext import kombu as kombux
from ...internal.utils import get_argument_value
from ...internal.utils.wrappers import unwrap
from ...pin import Pin
from ...propagation.http import HTTPPropagator

# project
from .. import trace_utils
from .constants import DEFAULT_SERVICE
from .utils import HEADER_POS
from .utils import extract_conn_tags
from .utils import get_body_length_from_args
from .utils import get_exchange_from_args
from .utils import get_routing_key_from_args


def _get_version():
    # type: () -> str
    return str(kombu.__version__)


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


# kombu default settings

config._add(
    "kombu",
    {
        "distributed_tracing_enabled": asbool(os.getenv("DD_KOMBU_DISTRIBUTED_TRACING", default=True)),
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
    kombu._datadog_patch = True

    _w = wrapt.wrap_function_wrapper
    # We wrap the _publish method because the publish method:
    # *  defines defaults in its kwargs
    # *  potentially overrides kwargs with values from self
    # *  extracts/normalizes things like exchange
    _w("kombu", "Producer._publish", _traced_publish)
    _w("kombu", "Consumer.receive", _traced_receive)

    # We do not provide a service for producer spans since they represent
    # external calls to another service.
    # Instead the service should be inherited from the parent.
    if config.service:
        prod_service = None
    # DEV: backwards-compatibility for users who set a kombu service
    else:
        prod_service = os.getenv("DD_KOMBU_SERVICE_NAME", default=DEFAULT_SERVICE)

    Pin(
        service=schematize_service_name(prod_service),
    ).onto(kombu.messaging.Producer)

    Pin(service=schematize_service_name(config.kombu["service_name"])).onto(kombu.messaging.Consumer)


def unpatch():
    if getattr(kombu, "_datadog_patch", False):
        kombu._datadog_patch = False
        unwrap(kombu.Producer, "_publish")
        unwrap(kombu.Consumer, "receive")


#
# tracing functions
#


def _traced_receive(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    # Signature only takes 2 args: (body, message)
    message = get_argument_value(args, kwargs, 1, "message")

    trace_utils.activate_distributed_headers(pin.tracer, request_headers=message.headers, int_config=config.kombu)

    with pin.tracer.trace(
        schematize_messaging_operation(kombux.RECEIVE_NAME, provider="kombu", direction=SpanDirection.PROCESSING),
        service=pin.service,
        span_type=SpanTypes.WORKER,
    ) as s:
        s.set_tag_str(COMPONENT, config.kombu.integration_name)

        # set span.kind to the type of operation being performed
        s.set_tag_str(SPAN_KIND, SpanKind.CONSUMER)

        s.set_tag(SPAN_MEASURED_KEY)
        # run the command
        exchange = message.delivery_info["exchange"]
        s.resource = exchange
        s.set_tag_str(kombux.EXCHANGE, exchange)

        s.set_tags(extract_conn_tags(message.channel.connection))
        s.set_tag_str(kombux.ROUTING_KEY, message.delivery_info["routing_key"])
        # set analytics sample rate
        s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.kombu.get_analytics_sample_rate())
        result = func(*args, **kwargs)
        core.dispatch("kombu.amqp.receive.post", [instance, message, s])
        return result


def traced_receive(func, instance, args, kwargs):
    deprecate(
        "traced_receive is deprecated",
        message="traced_receive is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _traced_receive(func, instance, args, kwargs)


def _traced_publish(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with pin.tracer.trace(
        schematize_messaging_operation(kombux.PUBLISH_NAME, provider="kombu", direction=SpanDirection.OUTBOUND),
        service=pin.service,
        span_type=SpanTypes.WORKER,
    ) as s:
        s.set_tag_str(COMPONENT, config.kombu.integration_name)

        # set span.kind to the type of operation being performed
        s.set_tag_str(SPAN_KIND, SpanKind.PRODUCER)

        s.set_tag(SPAN_MEASURED_KEY)
        exchange_name = get_exchange_from_args(args)
        s.resource = exchange_name
        s.set_tag_str(kombux.EXCHANGE, exchange_name)
        if pin.tags:
            s.set_tags(pin.tags)
        s.set_tag_str(kombux.ROUTING_KEY, get_routing_key_from_args(args))
        s.set_tags(extract_conn_tags(instance.channel.connection))
        s.set_metric(kombux.BODY_LEN, get_body_length_from_args(args))
        # set analytics sample rate
        s.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.kombu.get_analytics_sample_rate())
        # run the command
        if config.kombu.distributed_tracing_enabled:
            propagator.inject(s.context, args[HEADER_POS])
        core.dispatch(
            "kombu.amqp.publish.pre", [args, kwargs, s]
        )  # Has to happen after trace injection for actual payload size
        return func(*args, **kwargs)


def traced_publish(func, instance, args, kwargs):
    deprecate(
        "traced_publish is deprecated",
        message="traced_publish is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _traced_publish(func, instance, args, kwargs)

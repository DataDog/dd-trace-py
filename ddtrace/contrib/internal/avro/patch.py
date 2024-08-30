import avro
import wrapt

from ddtrace import config
from ddtrace.contrib import trace_utils
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.pin import Pin

from .schema_iterator import SchemaExtractor


config._add(
    "avro",
    dict(
        _default_service=schematize_service_name("avro"),
    ),
)


def get_version():
    # type: () -> str
    return getattr(avro, "__version__", "")


def patch():
    """Patch the instrumented methods"""
    if getattr(avro, "_datadog_patch", False):
        return
    avro._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w("avro.io", "DatumReader.read", traced_deserialize)
    _w("avro.io", "DatumWriter.write", traced_serialize)
    Pin(service=None).onto(avro.io.DatumReader)
    Pin(service=None).onto(avro.io.DatumWriter)


def unpatch():
    if getattr(avro, "_datadog_patch", False):
        avro._datadog_patch = False

        unwrap(avro.io.DatumReader, "read")
        unwrap(avro.io.DatumWriter, "write")


#
# tracing functions
#
def traced_serialize(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with core.context_with_data(
        "avro.write",
        span_name=SchemaExtractor.SERIALIZATION,
        pin=pin,
        service=trace_utils.ext_service(pin, config.avro),
        span_type=SpanTypes.REDIS,
        resource=SchemaExtractor.SERIALIZATION,
        call_key="avro_write",
    ) as ctx, ctx[ctx["call_key"]] as span:
        # _set_span_tags(span, pin, config_integration, args, instance, query)

        try:
            func(*args, **kwargs)
        finally:
            if config._data_streams_enabled and span:
                SchemaExtractor.attach_schema_on_span(instance.writers_schema, span, SchemaExtractor.SERIALIZATION)
            # core.dispatch("redis.async_command.post", [ctx, rowcount])


def traced_deserialize(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    with core.context_with_data(
        "avro.read",
        span_name=SchemaExtractor.DESERIALIZATION,
        pin=pin,
        service=trace_utils.ext_service(pin, config.avro),
        span_type="reader",
        resource=SchemaExtractor.DESERIALIZATION,
        call_key="avro_read",
    ) as ctx, ctx[ctx["call_key"]] as span:
        # _set_span_tags(span, pin, config_integration, args, instance, query)

        result = None
        try:
            result = func(*args, **kwargs)
            return result
        finally:
            if config._data_streams_enabled and span:
                reader = instance
                if reader:
                    SchemaExtractor.attach_schema_on_span(reader.writers_schema, span, SchemaExtractor.DESERIALIZATION)
            # core.dispatch("redis.async_command.post", [ctx, rowcount])

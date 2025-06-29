from typing import Dict

import avro
import wrapt

from ddtrace import config
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.trace import Pin

from .schema_iterator import SchemaExtractor


config._add(
    "avro",
    dict(),
)


def get_version():
    # type: () -> str
    return getattr(avro, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"avro": "*"}


def patch():
    """Patch the instrumented methods"""
    if getattr(avro, "_datadog_patch", False):
        return
    avro._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w("avro.io", "DatumReader.read", _traced_deserialize)
    _w("avro.io", "DatumWriter.write", _traced_serialize)
    Pin().onto(avro.io.DatumReader)
    Pin().onto(avro.io.DatumWriter)


def unpatch():
    if getattr(avro, "_datadog_patch", False):
        avro._datadog_patch = False

        unwrap(avro.io.DatumReader, "read")
        unwrap(avro.io.DatumWriter, "write")


#
# tracing functions
#
def _traced_serialize(func, instance, args, kwargs):
    # this is a dsm only integration at the moment
    if not config._data_streams_enabled:
        return func(*args, **kwargs)

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    active = pin.tracer.current_span()

    try:
        func(*args, **kwargs)
    finally:
        if active:
            SchemaExtractor.attach_schema_on_span(instance.writers_schema, active, SchemaExtractor.SERIALIZATION)


def _traced_deserialize(func, instance, args, kwargs):
    # this is a dsm only integration at the moment
    if not config._data_streams_enabled:
        return func(*args, **kwargs)

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    active = pin.tracer.current_span()

    try:
        func(*args, **kwargs)
    finally:
        reader = instance
        if active and reader:
            SchemaExtractor.attach_schema_on_span(reader.writers_schema, active, SchemaExtractor.DESERIALIZATION)

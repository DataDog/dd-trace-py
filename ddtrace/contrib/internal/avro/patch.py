import avro
import wrapt

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.trace import tracer

from .schema_iterator import SchemaExtractor


config._add(
    "avro",
    dict(),
)


def get_version() -> str:
    return getattr(avro, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"avro": "*"}


def patch():
    """Patch the instrumented methods"""
    if getattr(avro, "_datadog_patch", False):
        return
    avro._datadog_patch = True

    _w = wrapt.wrap_function_wrapper

    _w("avro.io", "DatumReader.read", _traced_deserialize)
    _w("avro.io", "DatumWriter.write", _traced_serialize)


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

    if not is_tracing_enabled():
        return func(*args, **kwargs)

    active = tracer.current_span()

    try:
        return func(*args, **kwargs)
    finally:
        if active:
            SchemaExtractor.attach_schema_on_span(instance.writers_schema, active, SchemaExtractor.SERIALIZATION)


def _traced_deserialize(func, instance, args, kwargs):
    # this is a dsm only integration at the moment
    if not config._data_streams_enabled:
        return func(*args, **kwargs)

    if not is_tracing_enabled():
        return func(*args, **kwargs)

    active = tracer.current_span()

    try:
        return func(*args, **kwargs)
    finally:
        reader = instance
        if active and reader:
            SchemaExtractor.attach_schema_on_span(reader.writers_schema, active, SchemaExtractor.DESERIALIZATION)

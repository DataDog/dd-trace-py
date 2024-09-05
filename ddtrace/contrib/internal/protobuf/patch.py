from google import protobuf
from google.protobuf.internal import builder
import wrapt

from ddtrace import config
from ddtrace.internal.utils.wrappers import unwrap
from ddtrace.pin import Pin

from .schema_iterator import SchemaExtractor


config._add(
    "protobuf",
    dict(),
)


def get_version():
    # type: () -> str
    return getattr(protobuf, "__version__", "")


def patch():
    """Patch the instrumented methods"""
    if getattr(protobuf, "_datadog_patch", False):
        return
    protobuf._datadog_patch = True
    breakpoint()
    _w = wrapt.wrap_function_wrapper

    _w("google.protobuf.internal", "builder.BuildTopDescriptorsAndMessages", _traced_build)
    # _w("avro.io", "DatumWriter.write", _traced_serialize)
    # Pin(service=None).onto(avro.io.DatumReader)
    # Pin(service=None).onto(avro.io.DatumWriter)


def unpatch():
    if getattr(avro, "_datadog_patch", False):
        avro._datadog_patch = False

        unwrap(avro.io.DatumReader, "read")
        unwrap(avro.io.DatumWriter, "write")


#
# tracing functions
#
def _traced_build(func, instance, args, kwargs):
    file_des = args[0]

    def wrap_message(message_descriptor, message_class):
        def wrapper(wrapped, instance, args, kwargs):
            return _traced_serialize_message(wrapped, instance, args, kwargs, msg_descriptor=message_descriptor)

        _w = wrapt.wrap_function_wrapper
        _w(message_class, "SerializeToString", wrapper)
        # Assuming Pin is defined elsewhere in your code
        Pin(service=None).onto(message_class)

    # pin = Pin.get_from(instance)
    # if not pin or not pin.enabled():
    #     return func(*args, **kwargs)

    # active = pin.tracer.current_span()

    try:
        func(*args, **kwargs)
    finally:
        generated_message_classes = args[2]
        message_descriptors = file_des.message_types_by_name.items()
        for message_idx in range(len(message_descriptors)):
            message_class_name = message_descriptors[message_idx][0]
            message_descriptor = message_descriptors[message_idx][1]
            message_class = generated_message_classes[message_class_name]
            wrap_message(message_descriptor=message_descriptor, message_class=message_class)
        if config._data_streams_enabled:
            pass
            # SchemaExtractor.attach_schema_on_span(instance.writers_schema, active, SchemaExtractor.SERIALIZATION)


def _traced_deserialize(func, instance, args, kwargs):
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    active = pin.tracer.current_span()

    try:
        func(*args, **kwargs)
    finally:
        if config._data_streams_enabled and active:
            reader = instance
            if reader:
                SchemaExtractor.attach_schema_on_span(reader.writers_schema, active, SchemaExtractor.DESERIALIZATION)


def _traced_serialize_message(func, instance, args, kwargs, msg_descriptor):
    breakpoint()
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled() or not msg_descriptor:
        return func(*args, **kwargs)

    active = pin.tracer.current_span()

    try:
        func(*args, **kwargs)
    finally:
        # if config._data_streams_enabled and active:
        SchemaExtractor.attach_schema_on_span(msg_descriptor, active, SchemaExtractor.SERIALIZATION)

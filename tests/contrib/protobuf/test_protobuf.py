import importlib

from ddtrace.constants import AUTO_KEEP
from ddtrace.contrib.internal.protobuf.patch import patch
from ddtrace.contrib.internal.protobuf.patch import unpatch
from ddtrace.ext import schema as SCHEMA_TAGS
from ddtrace.internal.compat import is_wrapted
from tests.contrib.protobuf.schemas import message_pb2
from tests.contrib.protobuf.schemas import other_message_pb2


MESSAGE_SCHEMA_DEF = (
    '{"openapi": "3.0.0", "components": {"schemas": {"MyMessage": {"type": "object", "properties": {"id": {"type": '
    '"string"}, "value": {"type": "string"}, "other_message": {"$ref": "#/components/schemas/OtherMessage"}, '
    '"status": {"type": "string", "format": "enum", "enum": ["UNKNOWN", "ACTIVE", "INACTIVE", "DELETED"]}}}, '
    '"OtherMessage": {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer", '
    '"format": "int32"}}}}}}'
)
MESSAGE_SCHEMA_ID = "6833269440911322626"

OTHER_MESSAGE_SCHEMA_DEF = (
    '{"openapi": "3.0.0", "components": {"schemas": {"OtherMessage": {"type": "object", "properties": {"name": '
    '{"type": "string"}, "age": {"type": "integer", "format": "int32"}}}}}}'
)
OTHER_MESSAGE_SCHEMA_ID = "2475724054364642627"


def test_unpatch_does_not_raise_after_message_class_wrapped():
    """
    Regression test to confirm that unpatch() should not throw AttributeErrors
    on SerializeToString since Protobuf 5.x+ had a change in the upb/C extension.
    """
    from tests.utils import override_global_config

    with override_global_config({"_data_streams_enabled": True}):
        patch()
        importlib.reload(other_message_pb2)  # _traced_build can trigger errors
        OtherMessage = other_message_pb2.OtherMessage
        other_message = OtherMessage()
        other_message.SerializeToString()
        unpatch()  # should not raise AttributeErrors


def test_patching(protobuf):
    """
    When patching protobuf library
        We wrap the correct methods
    When unpatching protobuf library
        We unwrap the correct methods
    """
    patch()
    assert is_wrapted(protobuf.internal.builder.BuildTopDescriptorsAndMessages)

    unpatch()
    assert not is_wrapted(protobuf.internal.builder.BuildTopDescriptorsAndMessages)


def test_basic_schema_serialize(protobuf, tracer, test_spans):
    importlib.reload(other_message_pb2)
    OtherMessage = other_message_pb2.OtherMessage

    other_message = OtherMessage()
    other_message.name.append("Alice")
    other_message.age = 30

    # Serialize
    with tracer.trace("other_message.serialize") as span:
        span.context.sampling_priority = AUTO_KEEP
        other_message.SerializeToString()

    assert len(test_spans.spans) == 1, "There should be exactly one trace"
    span = test_spans.spans[0]

    # Perform the assertions
    assert span.name == "other_message.serialize"
    assert span.error == 0

    tags = span.get_tags()
    metrics = span.get_metrics()
    assert tags[SCHEMA_TAGS.SCHEMA_DEFINITION] == OTHER_MESSAGE_SCHEMA_DEF
    assert tags[SCHEMA_TAGS.SCHEMA_TYPE] == "protobuf"
    assert tags[SCHEMA_TAGS.SCHEMA_NAME] == "OtherMessage"
    assert tags[SCHEMA_TAGS.SCHEMA_OPERATION] == "serialization"
    assert tags[SCHEMA_TAGS.SCHEMA_ID] == OTHER_MESSAGE_SCHEMA_ID
    assert metrics[SCHEMA_TAGS.SCHEMA_WEIGHT] == 1


def test_complex_schema_serialize(protobuf, tracer, test_spans):
    importlib.reload(other_message_pb2)
    importlib.reload(message_pb2)
    OtherMessage = other_message_pb2.OtherMessage
    MyMessage = message_pb2.MyMessage
    Status = message_pb2.Status

    my_message = MyMessage()

    # Set the id and value fields
    my_message.id = "123"
    my_message.value = "example_value"
    my_message.status = Status.ACTIVE

    # Create instances of OtherMessage
    other_message1 = OtherMessage()
    other_message1.name.append("Alice")
    other_message1.age = 30

    other_message2 = OtherMessage()
    other_message2.name.append("Bob")
    other_message2.age = 25

    # Add OtherMessage instances to the other_message field of MyMessage
    my_message.other_message.append(other_message1)
    my_message.other_message.append(other_message2)

    # Serialize
    with tracer.trace("message_pb2.serialize") as span:
        span.context.sampling_priority = AUTO_KEEP
        my_message.SerializeToString()

    assert len(test_spans.spans) == 1, "There should be exactly one trace"
    span = test_spans.spans[0]

    # Perform the assertions
    assert span.name == "message_pb2.serialize"
    assert span.error == 0

    tags = span.get_tags()
    metrics = span.get_metrics()
    assert tags[SCHEMA_TAGS.SCHEMA_DEFINITION] == MESSAGE_SCHEMA_DEF
    assert tags[SCHEMA_TAGS.SCHEMA_TYPE] == "protobuf"
    assert tags[SCHEMA_TAGS.SCHEMA_NAME] == "MyMessage"
    assert tags[SCHEMA_TAGS.SCHEMA_OPERATION] == "serialization"
    assert tags[SCHEMA_TAGS.SCHEMA_ID] == MESSAGE_SCHEMA_ID
    assert metrics[SCHEMA_TAGS.SCHEMA_WEIGHT] == 1


def test_basic_schema_deserialize(protobuf, tracer, test_spans):
    importlib.reload(other_message_pb2)
    OtherMessage = other_message_pb2.OtherMessage

    other_message = OtherMessage()
    other_message.name.append("Alice")
    other_message.age = 30

    # Serialize
    bytes_data = other_message.SerializeToString()

    # Deserialize
    with tracer.trace("other_message.deserialize") as span:
        span.context.sampling_priority = AUTO_KEEP
        other_message.ParseFromString(bytes_data)

    assert len(test_spans.spans) == 1, "There should be exactly one span"

    span = test_spans.spans[0]

    # Perform the assertions
    assert span.name == "other_message.deserialize"
    assert span.error == 0

    tags = span.get_tags()
    metrics = span.get_metrics()
    assert tags[SCHEMA_TAGS.SCHEMA_DEFINITION] == OTHER_MESSAGE_SCHEMA_DEF
    assert tags[SCHEMA_TAGS.SCHEMA_TYPE] == "protobuf"
    assert tags[SCHEMA_TAGS.SCHEMA_NAME] == "OtherMessage"
    assert tags[SCHEMA_TAGS.SCHEMA_OPERATION] == "deserialization"
    assert tags[SCHEMA_TAGS.SCHEMA_ID] == OTHER_MESSAGE_SCHEMA_ID
    assert metrics[SCHEMA_TAGS.SCHEMA_WEIGHT] == 1


def test_advanced_schema_deserialize(protobuf, tracer, test_spans):
    importlib.reload(other_message_pb2)
    importlib.reload(message_pb2)
    OtherMessage = other_message_pb2.OtherMessage
    MyMessage = message_pb2.MyMessage
    Status = message_pb2.Status

    my_message = MyMessage()

    # Set the id and value fields
    my_message.id = "123"
    my_message.value = "example_value"
    my_message.status = Status.ACTIVE

    # Create instances of OtherMessage
    other_message1 = OtherMessage()
    other_message1.name.append("Alice")
    other_message1.age = 30

    other_message2 = OtherMessage()
    other_message2.name.append("Bob")
    other_message2.age = 25

    # Add OtherMessage instances to the other_message field of MyMessage
    my_message.other_message.append(other_message1)
    my_message.other_message.append(other_message2)

    # Serialize
    bytes_data = my_message.SerializeToString()

    # Deserialize
    with tracer.trace("my_message.deserialize") as span:
        span.context.sampling_priority = AUTO_KEEP
        my_message.ParseFromString(bytes_data)

    assert len(test_spans.spans) == 1, "There should be exactly one span"

    assert len(test_spans.spans) == 1, "There should be exactly one span"

    # Get the first (and only) span
    span = test_spans.spans[0]

    # Perform the assertions
    assert span.name == "my_message.deserialize"
    assert span.error == 0

    tags = span.get_tags()
    metrics = span.get_metrics()
    assert tags[SCHEMA_TAGS.SCHEMA_DEFINITION] == MESSAGE_SCHEMA_DEF
    assert tags[SCHEMA_TAGS.SCHEMA_TYPE] == "protobuf"
    assert tags[SCHEMA_TAGS.SCHEMA_NAME] == "MyMessage"
    assert tags[SCHEMA_TAGS.SCHEMA_OPERATION] == "deserialization"
    assert tags[SCHEMA_TAGS.SCHEMA_ID] == MESSAGE_SCHEMA_ID
    assert metrics[SCHEMA_TAGS.SCHEMA_WEIGHT] == 1

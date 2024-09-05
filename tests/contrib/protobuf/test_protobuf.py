from google.protobuf.message import DecodeError
from wrapt import ObjectProxy

from ddtrace import Pin
from ddtrace.contrib.protobuf.patch import patch
from ddtrace.contrib.protobuf.patch import unpatch
from ddtrace.ext import schema as SCHEMA_TAGS


OPENAPI_USER_SCHEMA_DEF = (
    '{"openapi": "3.0.0", "components": {"schemas": {"example.avro.User": {"type": "object", "properties": '
    '{"name": {"type": "string"}, "favorite_number": {"type": "union[integer,null]"}, "favorite_color": '
    '{"type": "union[string,null]"}}}}}}'
)

OPENAPI_ADVANCED_USER_SCHEMA_DEF = (
    '{"openapi": "3.0.0", "components": {"schemas": {"example.avro.AdvancedUser": {"type": "object", "properties": '
    '{"name": {"type": "string"}, "age": {"type": "integer"}, "email": {"type": "union[null,string]"}, "height": '
    '{"type": "number"}, "preferences": {"type": "object"}, "tags": {"type": "array"}, "status": {"type": "string"}, '
    '"profile_picture": {"type": "string"}, "metadata": {"type": "string"}, "address": {"type": "object"}}}}}}'
)

MESSAGE_SCHEMA_DEF = '{"openapi": "3.0.0", "components": {"schemas": {"MyMessage": {"type": "object", "properties": {"id": {"type": "string"}, "value": {"type": "string"}, "other_message": {"$ref": "#/components/schemas/OtherMessage"}}}, "OtherMessage": {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer", "format": "int32"}}}}}}'
MESSAGE_SCHEMA_ID = "4297837876786368340"


# def test_patching(protobuf):
#     """
#     When patching avro library
#         We wrap the correct methods
#     When unpatching avro library
#         We unwrap the correct methods
#     """
#     patch()
#     assert isinstance(protobuf.io.DatumReader.read, ObjectProxy)
#     assert isinstance(protobuf.io.DatumWriter.write, ObjectProxy)

#     unpatch()

#     assert not isinstance(protobuf.io.DatumReader.read, ObjectProxy)
#     assert not isinstance(protobuf.io.DatumWriter.write, ObjectProxy)


def test_extract_protobuf_schema_on_serialize(protobuf, tracer, test_spans):
    from tests.contrib.protobuf.schemas.message_pb2 import MyMessage
    from tests.contrib.protobuf.schemas.other_message_pb2 import OtherMessage

    my_message = OtherMessage()

    # Set the id and value fields
    my_message.id = "123"
    my_message.value = "example_value"

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
    with tracer.trace("message_pb2.serialize"):
        breakpoint()
        bytes_data = my_message.SerializeToString()

    assert len(test_spans.spans) == 1, "There should be exactly one trace"
    breakpoint()
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


def test_error_when_de_serializing(self):
    with tracer.trace("parent_deserialize") as span:
        span.set_tag("manual.keep", True)
        try:
            MyMessage.ParseFromString(b'\x01\x02\x03\x04\x05')
        except DecodeError as e:
            span.set_tag("error.msg", str(e))
            span.set_tag("error.type", type(e).__name__)
            span.set_tag("error.stack", e.__traceback__)
            span.error = 1


def test_complex_schema_serialize(protobuf, tracer, test_spans):
    from tests.contrib.protobuf.schemas.message_pb2 import MyMessage
    from tests.contrib.protobuf.schemas.other_message_pb2 import OtherMessage

    my_message = MyMessage()

    # Set the id and value fields
    my_message.id = "123"
    my_message.value = "example_value"

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
    with tracer.trace("message_pb2.serialize"):
        breakpoint()
        bytes_data = my_message.SerializeToString()

    assert len(test_spans.spans) == 1, "There should be exactly one trace"
    breakpoint()
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

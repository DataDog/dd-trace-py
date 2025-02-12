from avro.datafile import DataFileReader
from avro.datafile import DataFileWriter
from avro.io import DatumReader
from avro.io import DatumWriter
from wrapt import ObjectProxy

from ddtrace.constants import AUTO_KEEP
from ddtrace.contrib.internal.avro.patch import patch
from ddtrace.contrib.internal.avro.patch import unpatch
from ddtrace.ext import schema as SCHEMA_TAGS
from ddtrace.trace import Pin


OPENAPI_USER_SCHEMA_DEF = (
    '{"openapi": "3.0.0", "components": {"schemas": {"example.avro.User": {"type": "object", "properties": '
    '{"name": {"type": "string"}, "favorite_number": {"type": "union[integer,null]"}, "favorite_color": '
    '{"type": "union[string,null]"}}}}}}'
)

OPENAPI_ADVANCED_USER_SCHEMA_DEF = (
    '{"openapi": "3.0.0", "components": {"schemas": {"example.avro.AdvancedUser": {"type": "object", "properties": '
    '{"name": {"type": "string"}, "age": {"type": "integer"}, "email": {"type": "union[null,string]"}, "height": '
    '{"type": "number"}, "preferences": {"type": "object"}, "tags": {"type": "array", "items": {"format": "string"}}, '
    '"status": {"type": "string"}, "profile_picture": {"type": "string"}, "metadata": {"type": "string"}, "address": '
    '{"type": "object", "$ref": "#/components/schemas/Address"}}}, "example.avro.Address": {"type": "object", '
    '"properties": {"street": {"type": "string"}, "city": {"type": "string"}, "zipcode": {"type": "string"}}}}}}'
)


def test_patching(avro):
    """
    When patching avro library
        We wrap the correct methods
    When unpatching avro library
        We unwrap the correct methods
    """
    patch()
    assert isinstance(avro.io.DatumReader.read, ObjectProxy)
    assert isinstance(avro.io.DatumWriter.write, ObjectProxy)

    unpatch()

    assert not isinstance(avro.io.DatumReader.read, ObjectProxy)
    assert not isinstance(avro.io.DatumWriter.write, ObjectProxy)


def test_basic_schema_serialize(avro, tracer, test_spans):
    writer = DatumWriter()

    pin = Pin.get_from(writer)
    assert pin is not None
    pin._clone(tags={"cheese": "camembert"}, tracer=tracer).onto(writer)

    with tracer.trace("basic_avro_schema.serialization") as span:
        span.context.sampling_priority = AUTO_KEEP
        schema = avro.schema.parse(open("tests/contrib/avro/schemas/user.avsc", "rb").read())

        writer = DataFileWriter(open("tests/contrib/avro/schemas/users.avro", "wb"), writer, schema)
        writer.append({"name": "Alyssa", "favorite_number": 256})
        writer.close()

    assert len(test_spans.spans) == 1, "There should be exactly one trace"

    span = test_spans.spans[0]

    # Perform the assertions
    assert span.name == "basic_avro_schema.serialization"
    assert span.error == 0

    tags = span.get_tags()
    metrics = span.get_metrics()
    assert tags[SCHEMA_TAGS.SCHEMA_DEFINITION] == OPENAPI_USER_SCHEMA_DEF
    assert tags[SCHEMA_TAGS.SCHEMA_TYPE] == "avro"
    assert tags[SCHEMA_TAGS.SCHEMA_NAME] == "example.avro.User"
    assert tags[SCHEMA_TAGS.SCHEMA_OPERATION] == "serialization"
    assert tags[SCHEMA_TAGS.SCHEMA_ID] == "1605040621379664412"
    assert metrics[SCHEMA_TAGS.SCHEMA_WEIGHT] == 1


def test_advanced_schema_serialize(avro, tracer, test_spans):
    writer = DatumWriter()

    pin = Pin.get_from(writer)
    assert pin is not None
    pin._clone(tags={"cheese": "camembert"}, tracer=tracer).onto(writer)

    with tracer.trace("advanced_avro_schema.serialization") as span:
        span.context.sampling_priority = AUTO_KEEP
        schema = avro.schema.parse(open("tests/contrib/avro/schemas/advanced_user.avsc", "rb").read())

        writer = DataFileWriter(open("tests/contrib/avro/schemas/advanced_users.avro", "wb"), writer, schema)
        writer.append(
            {
                "name": "Alyssa",
                "age": 30,
                "email": "alyssa@example.com",
                "height": 5.6,
                "preferences": {"theme": "dark", "notifications": "enabled"},
                "tags": ["vip", "premium"],
                "status": "ACTIVE",
                "profile_picture": b"binarydata",
                "metadata": b"metadata12345678",
                "address": {"street": "123 Main St", "city": "Metropolis", "zipcode": "12345"},
            }
        )
        writer.close()

    assert len(test_spans.spans) == 1, "There should be exactly one trace"

    span = test_spans.spans[0]

    # Perform the assertions
    assert span.name == "advanced_avro_schema.serialization"
    assert span.error == 0

    tags = span.get_tags()
    metrics = span.get_metrics()
    assert tags[SCHEMA_TAGS.SCHEMA_DEFINITION] == OPENAPI_ADVANCED_USER_SCHEMA_DEF
    assert tags[SCHEMA_TAGS.SCHEMA_TYPE] == "avro"
    assert tags[SCHEMA_TAGS.SCHEMA_NAME] == "example.avro.AdvancedUser"
    assert tags[SCHEMA_TAGS.SCHEMA_OPERATION] == "serialization"
    assert tags[SCHEMA_TAGS.SCHEMA_ID] == "16919528191335747635"
    assert metrics[SCHEMA_TAGS.SCHEMA_WEIGHT] == 1


def test_basic_schema_deserialize(avro, tracer, test_spans):
    reader = DatumReader()

    pin = Pin.get_from(reader)
    assert pin is not None
    pin._clone(tags={"cheese": "camembert"}, tracer=tracer).onto(reader)

    with tracer.trace("basic_avro_schema.deserialization") as span:
        span.context.sampling_priority = AUTO_KEEP
        reader = DataFileReader(open("tests/contrib/avro/schemas/users.avro", "rb"), reader)
        for _ in reader:
            pass
        reader.close()

    assert len(test_spans.spans) == 1, "There should be exactly one span"

    span = test_spans.spans[0]

    # Perform the assertions
    assert span.name == "basic_avro_schema.deserialization"
    assert span.error == 0

    tags = span.get_tags()
    metrics = span.get_metrics()
    assert tags[SCHEMA_TAGS.SCHEMA_DEFINITION] == OPENAPI_USER_SCHEMA_DEF
    assert tags[SCHEMA_TAGS.SCHEMA_TYPE] == "avro"
    assert tags[SCHEMA_TAGS.SCHEMA_NAME] == "example.avro.User"
    assert tags[SCHEMA_TAGS.SCHEMA_OPERATION] == "deserialization"
    assert tags[SCHEMA_TAGS.SCHEMA_ID] == "1605040621379664412"
    assert metrics[SCHEMA_TAGS.SCHEMA_WEIGHT] == 1


def test_advanced_schema_deserialize(avro, tracer, test_spans):
    reader = DatumReader()

    pin = Pin.get_from(reader)
    assert pin is not None
    pin._clone(tags={"cheese": "camembert"}, tracer=tracer).onto(reader)

    with tracer.trace("advanced_avro_schema.deserialization") as span:
        span.context.sampling_priority = AUTO_KEEP
        reader = DataFileReader(open("tests/contrib/avro/schemas/advanced_users.avro", "rb"), reader)
        for _ in reader:
            pass
        reader.close()

    assert len(test_spans.spans) == 1, "There should be exactly one span"

    # Get the first (and only) span
    span = test_spans.spans[0]

    # Perform the assertions
    assert span.name == "advanced_avro_schema.deserialization"
    assert span.error == 0

    tags = span.get_tags()
    metrics = span.get_metrics()
    assert tags[SCHEMA_TAGS.SCHEMA_DEFINITION] == OPENAPI_ADVANCED_USER_SCHEMA_DEF
    assert tags[SCHEMA_TAGS.SCHEMA_TYPE] == "avro"
    assert tags[SCHEMA_TAGS.SCHEMA_NAME] == "example.avro.AdvancedUser"
    assert tags[SCHEMA_TAGS.SCHEMA_OPERATION] == "deserialization"
    assert tags[SCHEMA_TAGS.SCHEMA_ID] == "16919528191335747635"
    assert metrics[SCHEMA_TAGS.SCHEMA_WEIGHT] == 1

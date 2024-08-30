import avro
from avro.datafile import DataFileReader
from avro.datafile import DataFileWriter
from avro.io import DatumReader
from avro.io import DatumWriter
from wrapt import ObjectProxy

from ddtrace import Pin
from ddtrace.contrib.avro.patch import patch
from ddtrace.contrib.avro.patch import unpatch


OPENAPI_USER_SCHEMA_DEF = (
    '{"openapi": "3.0.0", "components": {"schemas": {"example.avro.User": {"type": "object", "properties": '
    '{"name": {"type": "string"}, "favorite_number": {"type": "string"}, "favorite_color": {"type": "string"}}}}}}'
)

OPENAPI_ADVANCED_USER_SCHEMA_DEF = (
    '{"openapi": "3.0.0", "components": {"schemas": {"example.avro.AdvancedUser": {"type": "object", "properties": '
    '{"name": {"type": "string"}, "age": {"type": "integer", "format": "int32"}, "email": {"type": "string", '
    '"nullable": true}, "height": {"type": "number", "format": "float"}, "preferences": {"type": "object", '
    '"additionalProperties": {"type": "string"}, "description": "Map type"}, "tags": {"type": "array", "items": '
    '{"type": "string"}}, "status": {"type": "string", "enum": ["ACTIVE", "INACTIVE", "BANNED"]}, "profile_picture": '
    '{"type": "string", "format": "byte"}, "metadata": {"type": "string"}, "address": {"type": "object", "properties": '
    '{"street": {"type": "string"}, "city": {"type": "string"}, "zipcode": {"type": "string"}}}}}}}}'
)


def test_patching():
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


def test_basic_schema_serialize(tracer, test_spans):
    writer = DatumWriter()

    pin = Pin.get_from(writer)
    assert pin is not None
    pin.clone(tags={"cheese": "camembert"}, tracer=tracer).onto(writer)

    schema = avro.schema.parse(open("schemas/user.avsc", "rb").read())

    writer = DataFileWriter(open("users.avro", "wb"), writer, schema)
    writer.append({"name": "Alyssa", "favorite_number": 256})
    writer.append({"name": "Ben", "favorite_number": 7, "favorite_color": "red"})
    writer.close()

    # Check if there is exactly one trace (in this context, one span)
    assert len(test_spans) == 1, "There should be exactly one trace"

    # Get the first (and only) span
    span = test_spans[0]

    # Perform the assertions
    assert span["serviceName"]
    assert span["operationName"] == "parent_serialize"
    assert span["resourceName"] == "parent_serialize"
    assert span["error"] == 0

    tags = span["tags"]
    assert tags["DDTags.SCHEMA_DEFINITION"] == OPENAPI_USER_SCHEMA_DEF
    assert tags["DDTags.SCHEMA_WEIGHT"] == 1
    assert tags["DDTags.SCHEMA_TYPE"] == "avro"
    assert tags["DDTags.SCHEMA_NAME"] == "example.avro.User"
    assert tags["DDTags.SCHEMA_OPERATION"] == "serialization"
    assert tags["DDTags.SCHEMA_ID"] == "schemaID"


def test_advanced_schema_serialize(tracer, test_spans):
    writer = DatumWriter()

    pin = Pin.get_from(writer)
    assert pin is not None
    pin.clone(tags={"cheese": "camembert"}, tracer=tracer).onto(writer)

    schema = avro.schema.parse(open("schemas/advanced_user.avsc", "rb").read())

    writer = DataFileWriter(open("advanced_users.avro", "wb"), writer, schema)
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

    # Check if there is exactly one trace (in this context, one span)
    assert len(test_spans) == 1, "There should be exactly one trace"

    # Get the first (and only) span
    span = test_spans[0]

    # Perform the assertions
    assert span["serviceName"]
    assert span["operationName"] == "parent_serialize"
    assert span["resourceName"] == "parent_serialize"
    assert span["error"] == 0

    tags = span["tags"]
    assert tags["DDTags.SCHEMA_DEFINITION"] == OPENAPI_ADVANCED_USER_SCHEMA_DEF
    assert tags["DDTags.SCHEMA_WEIGHT"] == 1
    assert tags["DDTags.SCHEMA_TYPE"] == "avro"
    assert tags["DDTags.SCHEMA_NAME"] == "example.avro.AdvancedUser"
    assert tags["DDTags.SCHEMA_OPERATION"] == "serialization"
    assert tags["DDTags.SCHEMA_ID"] == "schemaID"


def test_basic_schema_deserialize(tracer, test_spans):
    reader = DatumReader()

    pin = Pin.get_from(reader)
    assert pin is not None
    pin.clone(tags={"cheese": "camembert"}, tracer=tracer).onto(reader)

    reader = DataFileReader(open("users.avro", "rb"), reader)
    for _ in reader:
        pass
    reader.close()

    # Check if there is exactly one trace (in this context, one span)
    assert len(test_spans) == 1, "There should be exactly one trace"

    # Get the first (and only) span
    span = test_spans[0]

    # Perform the assertions
    assert span["serviceName"]
    assert span["operationName"] == "parent_deserialize"
    assert span["resourceName"] == "parent_deserialize"
    assert span["error"] == 0

    tags = span["tags"]
    assert tags["DDTags.SCHEMA_DEFINITION"] == OPENAPI_USER_SCHEMA_DEF
    assert tags["DDTags.SCHEMA_WEIGHT"] == 1
    assert tags["DDTags.SCHEMA_TYPE"] == "avro"
    assert tags["DDTags.SCHEMA_NAME"] == "example.avro.User"
    assert tags["DDTags.SCHEMA_OPERATION"] == "deserialization"
    assert tags["DDTags.SCHEMA_ID"] == "schemaID"

import json

from ddtrace.internal.datastreams.schemas.schema_builder import SchemaBuilder
from ddtrace.internal.datastreams.schemas.schema_iterator import SchemaIterator


class Iterator(SchemaIterator):
    def iterate_over_schema(self, builder: SchemaBuilder):
        builder.add_property("person", "name", False, "string", "name of the person", None, None, None)
        builder.add_property("person", "phone_numbers", True, "string", None, None, None, None)
        builder.add_property("person", "person_name", False, "string", None, None, None, None)
        builder.add_property("person", "address", False, "object", None, "#/components/schemas/address", None, None)
        builder.add_property("address", "zip", False, "number", None, None, "int", None)
        builder.add_property("address", "street", False, "string", None, None, None, None)


def test_schema_is_converted_correctly_to_json():
    builder = SchemaBuilder(Iterator())

    should_extract_person = builder.should_extract_schema("person", 0)
    should_extract_address = builder.should_extract_schema("address", 1)
    should_extract_person2 = builder.should_extract_schema("person", 0)
    should_extract_too_deep = builder.should_extract_schema("city", 11)
    schema = builder.build()

    expected_schema = {
        "components": {
            "schemas": {
                "person": {
                    "properties": {
                        "name": {"description": "name of the person", "type": "string"},
                        "phone_numbers": {"items": {"type": "string"}, "type": "array"},
                        "person_name": {"type": "string"},
                        "address": {"$ref": "#/components/schemas/address", "type": "object"},
                    },
                    "type": "object",
                },
                "address": {
                    "properties": {"zip": {"format": "int", "type": "number"}, "street": {"type": "string"}},
                    "type": "object",
                },
            }
        },
        "openapi": "3.0.0",
    }

    assert json.loads(schema.definition) == expected_schema
    assert schema.id == "9510078321201428652"
    assert should_extract_person
    assert should_extract_address
    assert not should_extract_person2
    assert not should_extract_too_deep

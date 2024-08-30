from ddtrace.ext import schema as SCHEMA_TAGS
from ddtrace.internal.datastreams.schemas.schema_builder import SchemaBuilder
from ddtrace.internal.datastreams.schemas.schema_iterator import SchemaIterator
from ddtrace.internal.datastreams.schemas.schema import Schema as DsmSchema
from ddtrace.internal.datastreams import data_streams_processor
from avro.schema import Schema as AvroSchema


dsm_processor = data_streams_processor()


class SchemaExtractor(SchemaIterator):
    SERIALIZATION = "serialization"
    DESERIALIZATION = "deserialization"
    AVRO = "avro"

    def __init__(self, schema: AvroSchema):
        self.schema = schema

    @staticmethod
    def extract_property(field, schema_name, field_name, builder, depth):
        array = False
        type_ = None
        format_ = None
        description = None
        ref = None
        enum_values = None

        field_type = field.schema.type
        if field_type == "RECORD":
            type_ = "object"
        elif field_type == "ENUM":
            type_ = "string"
            enum_values = field.schema.symbols
        elif field_type == "ARRAY":
            array = True
            type_ = SchemaExtractor.get_type(field.schema.items.type)
        elif field_type == "MAP":
            type_ = "object"
            description = "Map type"
        elif field_type == "STRING":
            type_ = "string"
        elif field_type == "BYTES":
            type_ = "string"
            format_ = "byte"
        elif field_type == "INT":
            type_ = "integer"
            format_ = "int32"
        elif field_type == "LONG":
            type_ = "integer"
            format_ = "int64"
        elif field_type == "FLOAT":
            type_ = "number"
            format_ = "float"
        elif field_type == "DOUBLE":
            type_ = "number"
            format_ = "double"
        elif field_type == "BOOLEAN":
            type_ = "boolean"
        elif field_type == "NULL":
            type_ = "null"
        elif field_type == "FIXED":
            type_ = "string"
        else:
            type_ = "string"
            description = "Unknown type"

        return builder.add_property(
            schema_name, field_name, array, type_, description, ref, format_, enum_values
        )

    @staticmethod
    def extract_schema(schema, builder, depth):
        depth += 1
        schema_name = schema.fullname
        if not builder.should_extract_schema(schema_name, depth):
            return False
        try:
            for field in schema.fields:
                if not SchemaExtractor.extract_property(field, schema_name, field.name, builder, depth):
                    return False
        except Exception:
            return False
        return True

    @staticmethod
    def extract_schemas(schema):
        return dsm_processor.get_schema(
            schema.fullname, SchemaExtractor(schema)
        )

    def iterate_over_schema(self, builder):
        self.extract_schema(self.schema, builder, 0)

    @staticmethod
    def attach_schema_on_span(schema, span, operation):
        if schema is None or span is None:
            return

        span.set_tag(SCHEMA_TAGS.SCHEMA_TYPE, SchemaExtractor.AVRO)
        span.set_tag(SCHEMA_TAGS.SCHEMA_NAME, schema.fullname)
        span.set_tag(SCHEMA_TAGS.SCHEMA_OPERATION, operation)

        if not dsm_processor.can_sample_schema(operation):
            return

        prio = span.force_sampling_decision()
        if prio is None or prio <= 0:
            return

        weight = dsm_processor.try_sample_schema(operation)
        if weight == 0:
            return

        schema_data = SchemaExtractor.extract_schemas(schema)
        span.set_tag(SCHEMA_TAGS.SCHEMA_DEFINITION, schema_data.definition)
        span.set_tag(SCHEMA_TAGS.SCHEMA_WEIGHT, weight)
        span.set_tag(SCHEMA_TAGS.SCHEMA_ID, schema_data.id)

    @staticmethod
    def get_type(type_):
        return {
            "string": "string",
            "int": "integer",
            "long": "integer",
            "float": "number",
            "double": "number",
            "boolean": "boolean",
            "null": "null",
        }.get(type_, "string")
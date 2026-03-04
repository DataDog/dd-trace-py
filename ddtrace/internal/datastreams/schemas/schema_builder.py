from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
import json
from typing import Any
from typing import Optional

from ddtrace.internal.utils.fnv import fnv1_64

from .schema import Schema
from .schema_iterator import SchemaIterator


class SchemaBuilder:
    max_depth = 10
    max_properties = 1000
    CACHE: dict[str, Schema] = {}
    properties = 0

    def __init__(self, iterator: SchemaIterator) -> None:
        self.schema: OpenApiSchema = OpenApiSchema()
        self.iterator: SchemaIterator = iterator

    def add_property(
        self,
        schema_name: str,
        field_name: str,
        is_array: bool,
        type_: Optional[str],
        description: Optional[str],
        ref: Optional[str],
        format_: Optional[str],
        enum_values: Optional[list[Any]],
    ) -> bool:
        if self.properties >= self.max_properties:
            return False
        self.properties += 1
        _property = OpenApiSchema.Property(type_, description, ref, format_, enum_values, None)
        if is_array:
            _property = OpenApiSchema.Property("array", None, None, None, None, _property)
        self.schema.components.schemas[schema_name].properties[field_name] = _property
        return True

    def build(self):
        self.iterator.iterate_over_schema(self)
        no_nones = convert_to_json_compatible(self.schema)
        definition = json.dumps(no_nones, default=lambda o: o.__dict__)
        _id = str(fnv1_64(definition.encode("utf-8")))
        return Schema(definition, _id)

    def should_extract_schema(self, schema_name, depth):
        if depth > self.max_depth:
            return False
        if schema_name in self.schema.components.schemas:
            return False
        self.schema.components.schemas[schema_name] = OpenApiSchema.Schema()
        return True

    @staticmethod
    def get_schema(schema_name, iterator):
        if schema_name not in SchemaBuilder.CACHE:
            SchemaBuilder.CACHE[schema_name] = SchemaBuilder(iterator).build()
        return SchemaBuilder.CACHE[schema_name]


@dataclass
class OpenApiSchema:
    openapi: str = "3.0.0"
    components: "OpenApiSchema.Components" = field(default_factory=lambda: OpenApiSchema.Components())

    @dataclass
    class Property:
        type: Optional[str]
        description: Optional[str] = None
        ref: Optional[str] = field(default=None, metadata={"name": "$ref"})
        format: Optional[str] = None
        enum_values: Optional[list[str]] = field(default=None, metadata={"name": "enum"})
        items: Optional["OpenApiSchema.Property"] = None

    @dataclass
    class Schema:
        type: str = "object"
        properties: dict[str, "OpenApiSchema.Property"] = field(default_factory=dict)

    @dataclass
    class Components:
        schemas: dict[str, "OpenApiSchema.Schema"] = field(default_factory=dict)


def convert_to_json_compatible(obj: Any) -> Any:
    if isinstance(obj, list):
        return [convert_to_json_compatible(item) for item in obj if item is not None]
    elif isinstance(obj, dict):
        return {convert_key(k): convert_to_json_compatible(v) for k, v in obj.items() if v is not None}
    elif hasattr(obj, "__dataclass_fields__"):
        return {convert_key(k): convert_to_json_compatible(v) for k, v in asdict(obj).items() if v is not None}
    return obj


def convert_key(key: str) -> str:
    if key == "ref":
        return "$ref"
    elif key == "enum_values":
        return "enum"
    elif key == "_property":
        return "property"
    elif key == "_id":
        return "id"
    return key

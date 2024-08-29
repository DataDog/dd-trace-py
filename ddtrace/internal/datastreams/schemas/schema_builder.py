from dataclasses import dataclass
from dataclasses import field
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from cachetools import LRUCache
import fnv

from .schema import Schema


class SchemaBuilder:
    max_depth = 10
    max_properties = 1000
    CACHE = LRUCache(maxsize=32)

    def __init__(self, iterator):
        self.schema = self.OpenApiSchema()
        self.properties = 0
        self.iterator = iterator

    def add_property(self, schema_name, field_name, is_array, type_, description, ref, format_, enum_values):
        if self.properties >= self.max_properties:
            return False
        self.properties += 1
        property_ = self.OpenApiSchema.Property(type_, description, ref, format_, enum_values, None)
        if is_array:
            property_ = self.OpenApiSchema.Property("array", None, None, None, None, property_)
        self.schema.components.schemas[schema_name].properties[field_name] = property_
        return True

    def build(self):
        self.iterator.iterate_over_schema(self)
        definition = json.dumps(self.schema, default=lambda o: o.__dict__, indent=2)
        id = str(fnv.hash(definition.encode("utf-8"), algorithm=fnv.fnv1a_64))
        return Schema(definition, id)

    def should_extract_schema(self, schema_name, depth):
        if depth > self.max_depth:
            return False
        if schema_name in self.schema.components.schemas:
            return False
        self.schema.components.schemas[schema_name] = self.OpenApiSchema.Schema()
        return True

    @dataclass
    class OpenApiSchema:
        openapi: str = "3.0.0"
        components: Any = field(default_factory=lambda: SchemaBuilder.OpenApiSchema.Components())

        @dataclass
        class Components:
            schemas: Dict[str, Any] = field(default_factory=dict)

        @dataclass
        class Schema:
            type: str = "object"
            properties: Dict[str, Any] = field(default_factory=dict)

        @dataclass
        class Property:
            type: str
            description: Optional[str]

            # In Python, we use underscores instead of dollar signs for variable names
            ref: Optional[str] = field(default=None, metadata={"name": "$ref"})
            format: Optional[str] = None
            enum_values: Optional[List[str]] = field(default=None, metadata={"name": "enum"})
            items: Optional[Any] = None

    @staticmethod
    def get_schema(schema_name, iterator):
        if schema_name not in SchemaBuilder.CACHE:
            SchemaBuilder.CACHE[schema_name] = SchemaBuilder(iterator).build()
        return SchemaBuilder.CACHE[schema_name]

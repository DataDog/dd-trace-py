from __future__ import annotations

import abc
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .schema_builder import SchemaBuilder


class SchemaIterator:
    @abc.abstractmethod
    def iterate_over_schema(self, builder: SchemaBuilder) -> None:
        pass

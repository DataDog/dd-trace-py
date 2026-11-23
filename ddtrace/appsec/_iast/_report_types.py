import dataclasses
import json
from typing import Any
from typing import ClassVar
from typing import Optional
from typing import cast
import zlib


ATTRS_TO_SKIP = frozenset({"_ranges", "_evidences_with_no_sources", "dialect"})


class NotNoneDictable:
    """Serialize dataclasses without unset or internal fields."""

    __dataclass_fields__: ClassVar[dict[str, dataclasses.Field[Any]]]

    def _to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(
            self,
            dict_factory=lambda x: {k: v for k, v in x if v is not None and k not in ATTRS_TO_SKIP},
        )


@dataclasses.dataclass(eq=False)
class Evidence(NotNoneDictable):
    dialect: Optional[str] = None
    value: Optional[str] = None
    _ranges: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    valueParts: Optional[list[dict[str, Any]]] = None

    def _valueParts_hash(self) -> Optional[int]:
        if not self.valueParts:
            return None

        _hash = 0
        for part in self.valueParts:
            json_str = json.dumps(part, sort_keys=True)
            part_hash = zlib.crc32(json_str.encode())
            _hash ^= part_hash

        return _hash

    def __hash__(self) -> int:
        return hash((self.value, self._valueParts_hash()))

    def __eq__(self, other: object) -> bool:
        evidence = cast(Evidence, other)
        return self.value == evidence.value and self._valueParts_hash() == evidence._valueParts_hash()

from typing import Callable
from typing import Optional
from typing import Pattern
from typing import Protocol
from typing import TypedDict
from typing import Union

from .._taint_tracking import OriginType


class EvidenceLike(Protocol):
    value: Optional[str]
    dialect: Optional[str]


class SensitiveRange(TypedDict):
    start: int
    end: int


class SensitiveSource(Protocol):
    origin: Union[str, OriginType]
    name: str
    value: Optional[str]


class RedactableSource(SensitiveSource, Protocol):
    redacted: Optional[bool]
    pattern: Optional[str]


class TaintedRange(SensitiveRange):
    source: SensitiveSource
    length: int


class ValuePart(TypedDict, total=False):
    value: str
    source: int
    redacted: bool
    pattern: str


class RedactionResult(TypedDict):
    redacted_value_parts: list[ValuePart]
    redacted_sources: list[int]


SensitiveAnalyzer = Callable[
    [EvidenceLike, Optional[Pattern[str]], Optional[Pattern[str]], Optional[Pattern[bytes]]], list[SensitiveRange]
]

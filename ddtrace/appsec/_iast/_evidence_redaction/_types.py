from typing import Callable
from typing import Optional
from typing import Pattern
from typing import Protocol
from typing import TypedDict


class EvidenceLike(Protocol):
    value: Optional[str]
    dialect: Optional[str]


class SensitiveRange(TypedDict):
    start: int
    end: int


SensitiveAnalyzer = Callable[
    [EvidenceLike, Optional[Pattern[str]], Optional[Pattern[str]], Optional[Pattern[bytes]]], list[SensitiveRange]
]

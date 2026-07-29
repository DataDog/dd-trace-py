from typing import Iterable
from typing import Protocol


class HTTPResponse(Protocol):
    def getcode(self) -> int: ...

    def getheaders(self) -> Iterable[tuple[str, str]]: ...

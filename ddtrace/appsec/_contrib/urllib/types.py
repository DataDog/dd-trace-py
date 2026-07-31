from typing import Iterable
from typing import Mapping
from typing import Optional
from typing import Protocol


class Request(Protocol):
    def get_full_url(self) -> str: ...


class HTTPResponse(Protocol):
    length: Optional[int]
    headers: Mapping[str, str]
    fp: object
    status: int

    def getheaders(self) -> Iterable[tuple[str, str]]: ...

    def read(self) -> bytes: ...

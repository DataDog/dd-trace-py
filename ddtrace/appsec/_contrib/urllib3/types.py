from typing import Mapping
from typing import Optional
from typing import Protocol


class HTTPConnectionPool(Protocol):
    scheme: str
    host: str
    port: Optional[int]


class Response(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> object: ...

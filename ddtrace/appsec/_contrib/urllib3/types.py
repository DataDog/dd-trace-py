from typing import Protocol


class HTTPConnectionPool(Protocol):
    scheme: str
    host: object
    port: object

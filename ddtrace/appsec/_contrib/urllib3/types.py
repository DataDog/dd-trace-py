from typing import Mapping
from typing import Optional
from typing import Protocol


class HTTPConnectionPool(Protocol):
    scheme: str
    host: str
    port: Optional[int]


class Response(Protocol):
    """Shape of a ``requests.Response``.

    ``wrapped_request`` is installed on ``requests.Session.request`` as well as on urllib3's
    ``RequestMethods.request``, and only the requests flavour carries a response worth analyzing
    here. This is deliberately not urllib3's own ``HTTPResponse`` (which exposes ``status``).
    """

    status_code: int
    headers: Mapping[str, str]

    def json(self) -> object: ...

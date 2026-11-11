from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional
from typing import Union

from ddtrace.internal.core.events import Event


@dataclass
class DbQueryEvent(Event):
    """A database query shared by instrumentation and product subscribers."""

    event_name = "db.query"

    query: Union[str, bytes, bytearray, memoryview]
    span_name_prefix: str
    # Returns the connection encoding of byte queries. It reads the live connection, so subscribers call it during
    # dispatch, and only when they need it since the lookup has a cost and can fail.
    get_encoding: Optional[Callable[[], Optional[str]]] = None

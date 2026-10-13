from dataclasses import dataclass
from typing import Union

from ddtrace.internal.core.events import Event


@dataclass
class DbQueryEvent(Event):
    """A database query shared by instrumentation and product subscribers."""

    event_name = "db.query"

    # Byte queries hold UTF-8 text, other encodings are decoded to str by the instrumentation.
    query: Union[str, bytes]
    span_name_prefix: str

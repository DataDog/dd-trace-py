import codecs
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional
from typing import Union

from ddtrace.internal import core
from ddtrace.internal.core.events import Event
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


@dataclass
class DbQueryEvent(Event):
    """A database query shared by instrumentation and product subscribers."""

    event_name = "db.query"

    # Byte queries hold UTF-8 text, other encodings are decoded to str by decode_db_query.
    query: Union[str, bytes]
    span_name_prefix: str


def decode_db_query(
    query: Union[str, bytes, bytearray, memoryview], query_encoding: Optional[Callable[[], Optional[str]]] = None
) -> Union[str, bytes]:
    """Return the query as DbQueryEvent expects it, decoding bytes with the connection encoding.

    Decoding is best effort so that inspection is never skipped: undecodable bytes are replaced, and bytes
    are kept as UTF-8 when the encoding cannot be resolved. The encoding is only resolved for listeners.
    """
    if isinstance(query, str):
        return query
    if query_encoding is not None and core.has_listeners(DbQueryEvent.event_name):
        try:
            encoding = query_encoding()
            if encoding and codecs.lookup(encoding).name != "utf-8":
                return str(query, encoding, "replace")
        except Exception:
            log.debug("Failed to decode the query with the connection encoding, assuming UTF-8", exc_info=True)
    # The WAF only reads the exact bytes type as text, not bytearray
    return bytes(query)

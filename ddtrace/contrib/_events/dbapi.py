from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from typing import Optional
from typing import Union

from ddtrace._trace.events import DatabaseOperationEvent as DatabaseOperationEvent
from ddtrace._trace.events import DatabaseResultEvent as DatabaseResultEvent
from ddtrace.internal import core
from ddtrace.internal.core.events import Event


@dataclass
class DatabaseQueryPrepareEvent(Event):
    """Optional query preparation after operation metadata is available."""

    event_name = "database.query.prepare"

    context: core.ExecutionContext[DatabaseOperationEvent]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    supports_query_rewriting: bool = True


@dataclass
class DbQueryEvent(Event):
    """A database query shared by instrumentation and product subscribers."""

    event_name = "db.query"

    query: Union[str, bytes, bytearray, memoryview]
    span_name_prefix: str
    # Returns the connection encoding of byte queries. It reads the live connection, so subscribers call it during
    # dispatch, and only when they need it since the lookup has a cost and can fail.
    get_encoding: Optional[Callable[[], Optional[str]]] = None

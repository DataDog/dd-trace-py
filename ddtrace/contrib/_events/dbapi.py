from dataclasses import dataclass

from ddtrace.internal.core.events import Event


@dataclass
class DbApiEvent(Event):
    """A database query shared by instrumentation and product subscribers."""

    event_name = "dbapi.query"

    query: str
    span_name_prefix: str

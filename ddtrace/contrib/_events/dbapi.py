from dataclasses import dataclass

from ddtrace.internal.core.events import Event


@dataclass
class DbQueryEvent(Event):
    """A database query shared by instrumentation and product subscribers."""

    event_name = "db.query"

    query: str
    span_name_prefix: str

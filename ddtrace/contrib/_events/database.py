from dataclasses import dataclass
from typing import Optional

from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


@dataclass
class DbApiExecuteEvent(Event):
    event_name = "database.dbapi_execute"

    query: str = event_field()
    span_name_prefix: Optional[str] = event_field()

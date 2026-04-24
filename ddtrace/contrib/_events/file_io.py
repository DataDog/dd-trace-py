from dataclasses import dataclass

from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


@dataclass
class FileOpenEvent(Event):
    event_name = "file_io.open"

    filename: str = event_field()

from dataclasses import dataclass
from typing import ClassVar
from typing import Union

from ddtrace.internal.core.events import Event


@dataclass
class FileOpenEvent(Event):
    event_name: ClassVar[str] = "appsec.filesystem.open"

    filename: Union[str, bytes]

from dataclasses import dataclass
import os
from typing import Optional
from typing import Union

from ddtrace.internal.core.events import Event


@dataclass
class FileOpenEvent(Event):
    """A file-open attempt, dispatched synchronously before opening."""

    event_name = "filesystem.open"

    path: object
    stack_trace_boundary: Optional[str] = None

    @property
    def filename(self) -> Optional[Union[str, bytes]]:
        # Resolve lazily: __fspath__ runs application code.
        if isinstance(self.path, (str, bytes, os.PathLike)):
            return os.fspath(self.path)
        return None

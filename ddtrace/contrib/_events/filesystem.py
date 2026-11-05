from dataclasses import dataclass
import os
from typing import Optional
from typing import Union

from ddtrace.internal.core.events import Event


@dataclass
class FileOpenEvent(Event):
    """A file-open attempt, dispatched synchronously before the file is opened."""

    event_name = "filesystem.open"

    path: object

    @property
    def filename(self) -> Optional[Union[str, bytes]]:
        # Resolve lazily: __fspath__ is application code and must not run merely
        # because instrumentation is installed. Integer file descriptors are not paths.
        if isinstance(self.path, (str, bytes, os.PathLike)):
            return os.fspath(self.path)
        return None

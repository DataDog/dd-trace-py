from dataclasses import dataclass
import os
from typing import Optional
from typing import Union

from ddtrace.internal.core.events import Event


@dataclass
class FileOpenEvent(Event):
    """A builtins.open or pathlib.Path.open attempt, dispatched before opening.

    Consumers inspect the event synchronously and must not mutate or retain its path.
    Ordinary listener Exceptions are ignored; a core DDBlockException aborts the
    operation before any file is opened. Other BaseExceptions also propagate.
    Filename resolution is lazy and can execute application __fspath__ code on
    every access. File descriptors and other non-path inputs resolve to None.

    stack_trace_boundary identifies the producer frame to omit when reporting
    the application call site. It carries no product-specific interpretation.
    """

    event_name = "filesystem.open"

    path: object
    stack_trace_boundary: Optional[str] = None

    @property
    def filename(self) -> Optional[Union[str, bytes]]:
        # Resolve lazily: __fspath__ is application code and must not run merely
        # because instrumentation is installed. Integer file descriptors are not paths.
        if isinstance(self.path, (str, bytes, os.PathLike)):
            return os.fspath(self.path)
        return None

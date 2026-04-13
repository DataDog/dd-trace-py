"""Log forwarding for CI Visibility test sessions.

This package handles forwarding log data to the Datadog logs intake:

- :class:`LogsWriter` — async writer that batches and sends log events via HTTP.
- :class:`LogsHandler` — ``logging.Handler`` that captures Python log records.
- :class:`StderrCapture` — captures C-level stderr (fd 2) output from native libraries.

Shared intake constraints (per-entry size limit, truncation) are defined here
so that both LogsHandler and StderrCapture enforce them consistently.
"""

from ddtrace.testing.internal.logs._constants import MAX_MESSAGE_BYTES
from ddtrace.testing.internal.logs._constants import TRUNCATION_SUFFIX
from ddtrace.testing.internal.logs._handler import LogsHandler
from ddtrace.testing.internal.logs._stderr import StderrCapture
from ddtrace.testing.internal.logs._writer import LogsWriter


__all__ = [
    "LogsHandler",
    "LogsWriter",
    "MAX_MESSAGE_BYTES",
    "StderrCapture",
    "TRUNCATION_SUFFIX",
]

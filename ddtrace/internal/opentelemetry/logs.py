from typing import Optional

from opentelemetry._logs import Logger as OtelLogger
from opentelemetry._logs import LogRecord as OtelLogRecord
from opentelemetry._logs import LogsProvider as OtelLogsProvider


class Logger(OtelLogger):
    """Handles emitting events and logs via `LogRecord`."""

    def __init__(
        self,
        name: str,
        version: Optional[str] = None,
        schema_url: Optional[str] = None,
        attributes: Optional[dict] = None,
    ) -> None:
        super().__init__()
        self._name = name
        self._version = version
        self._schema_url = schema_url
        self._attributes = attributes

    def emit(self, record: OtelLogRecord) -> None:
        """Emit a log record."""
        pass


class LoggerProvider(OtelLogsProvider):
    def get_logger(
        self,
        name: str,
        version: Optional[str] = None,
        schema_url: Optional[str] = None,
        attributes: Optional[dict] = None,
    ) -> Logger:
        return Logger(name, version, schema_url, attributes)

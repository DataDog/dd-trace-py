import logging
from typing import Optional

from opentelemetry._logs import Logger as OtelLogger
from opentelemetry._logs import LogRecord as OtelLogRecord
from opentelemetry._logs import LoggerProvider as OtelLogsProvider
from opentelemetry._logs import get_logger_provider as otel_get_logger_provider

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.internal.logging.constants import RECORD_ATTR_ENV
from ddtrace.contrib.internal.logging.constants import RECORD_ATTR_SERVICE
from ddtrace.contrib.internal.logging.constants import RECORD_ATTR_SPAN_ID
from ddtrace.contrib.internal.logging.constants import RECORD_ATTR_TRACE_ID
from ddtrace.contrib.internal.logging.constants import RECORD_ATTR_VALUE_EMPTY
from ddtrace.contrib.internal.logging.constants import RECORD_ATTR_VALUE_ZERO
from ddtrace.contrib.internal.logging.constants import RECORD_ATTR_VERSION


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


class LoggingHandler(logging.StreamHandler):
    """A handler class which writes logging records, in OTLP format, to
    a network destination or file. Supports signals from the `logging` module.
    https://docs.python.org/3/library/logging.html
    """

    FORMATER = (
        "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] "
        "[dd.service=%(dd.service)s dd.env=%(dd.env)s "
        "dd.version=%(dd.version)s " + "dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] " + "- %(message)s"
    )

    def __init__(
        self,
        level=logging.NOTSET,
        logger_provider=None,
    ) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter(self.FORMATER))
        self._logger_provider = logger_provider or otel_get_logger_provider()

    def emit(self, record):
        setattr(record, RECORD_ATTR_VERSION, config.version or RECORD_ATTR_VALUE_EMPTY)
        setattr(record, RECORD_ATTR_ENV, config.env or RECORD_ATTR_VALUE_EMPTY)
        setattr(record, RECORD_ATTR_SERVICE, config.service or RECORD_ATTR_VALUE_EMPTY)
        trace_details = tracer.get_log_correlation_context()
        setattr(record, RECORD_ATTR_TRACE_ID, trace_details.get("trace_id", RECORD_ATTR_VALUE_ZERO))
        setattr(record, RECORD_ATTR_SPAN_ID, trace_details.get("span_id", RECORD_ATTR_VALUE_ZERO))
        super().emit(record)


import time
from opentracing import Span as OpenTracingSpan
from ddtrace.span import Span as DatadogSpan


class SpanLogRecord(object):
    """A representation of a log record."""

    slots = ['record', 'timestamp']

    def __init__(self, key_values, timestamp=None):
        self.timestamp = timestamp or time.time()
        self.record = key_values


class SpanLog(object):
    """A collection of log records."""

    slots = ['records']

    def __init__(self):
        self.records = []

    def add_record(self, key_values, timestamp=None):
        self.records.append(SpanLogRecord(key_values, timestamp))

    def __len__(self):
        return len(self.records)

    def __getitem__(self, key):
        if type(key) is int and key < len(self):
            return self.records[key]
        return None


class Span(OpenTracingSpan):
    """Datadog implementation of :class:`opentracing.Span`"""

    def __init__(self, tracer, context, operation_name):
        super(Span, self).__init__(tracer, context)

        # use a datadog span
        self._span = DatadogSpan(tracer._tracer, operation_name)

        self.log = SpanLog()

    def finish(self, finish_time=None):
        """"""
        # finish the datadog span
        self._span.finish()

    def set_baggage_item(self, key, value):
        """Sets a baggage item in the span context of this span.

        Baggage is used to propagate state between spans.

        :param key: baggage item key
        :type key: str

        :param value: baggage item value
        :type value: a type that can be compat.stringify()'d

        :rtype: Span
        :return: itself for chaining calls
        """
        self._context.set_baggage_item(key, value)
        return self

    def get_baggage_item(self, key):
        """Gets a baggage item from the span context of this span.

        :param key: baggage item key
        :type key: str

        :rtype: str
        :return: the baggage value for the given key or ``None``.
        """
        return self._context.get_baggage_item(key)

    def set_operation_name(self, operation_name):
        """Set the operation name."""
        self._span.name = operation_name

    def log_kv(self, key_values, timestamp=None):
        """Add a log record to this span.

        :param key_values: a dict of string keys and values of any type
        :type key_values: dict

        :param timestamp: a unix timestamp per time.time()
        :type timestamp: float

        :return: the span itself, for call chaining
        :rtype: Span
        """
        self.log.add_record(key_values, timestamp)
        return self

    def set_tag(self, key, value):
        """Set a tag on the span.

        This sets the tag on the underlying datadog span.
        """
        return self._span.set_tag(key, value)

    def get_tag(self, key):
        """Gets a tag from the span.

        This retrieves the tag from the underlying datadog span.
        """
        return self._span.get_tag(key)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._span.set_exc_info(exc_type, exc_val, exc_tb)

        self._span.__exit__(exc_type, exc_val, exc_tb)

        # note: self.finish AND _span.__exit__ will call _span.finish() but it is idempotent
        self.finish()

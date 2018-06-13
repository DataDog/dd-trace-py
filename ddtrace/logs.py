import logging

from . import helpers


class TraceContextFilter(logging.Filter):
    """Injects Tracing Correlation Identifiers in the current log record.
    This action is executed everytime a debug(), info() etc. are used, but
    it requires adding the ``Filter`` in all ``Handler``s. To add a Filter,
    simply:

        from ddtrace.logs import TraceContextFilter

        trace_filter = TraceContextFilter()
        for handler in logging.root.handlers:
            handler.addFilter(trace_filter)
    """
    def filter(self, record):
        trace_id, span_id = helpers.get_correlation_ids()
        record.trace_id = trace_id
        record.span_id = span_id
        return True

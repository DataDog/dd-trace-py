"""
Datadog instrumentation for AWS Lambda Runtime Interface Client (RIC).
"""

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.trace import Pin

log = get_logger(__name__)

config._add(
    "awslambdaric",
    dict(
        _default_service="awslambdaric",
    ),
)

class DatadogInstrumentation(object):
    """Patches an AWS Lambda handler function for Datadog instrumentation."""

    def __call__(self, func, args, kwargs):
        self.func = func
        self._before(args, kwargs)
        try:
            self.response = self.func(*args, **kwargs)
            return self.response
        finally:
            self._after()

    def _set_context(self, args, kwargs):
        """Sets the context attribute."""
        # The context is the second argument in a handler
        # signature and it is always sent.
        #
        # note: AWS Lambda context is an object, the event is a dict.
        # `get_remaining_time_in_millis` is guaranteed to be
        # present in the context.
        _context = get_argument_value(args, kwargs, 1, "context")
        if hasattr(_context, "get_remaining_time_in_millis"):
            self.context = _context
        else:
            # Handler was possibly manually wrapped, and the first
            # argument is the `datadog-lambda` decorator object.
            self.context = get_argument_value(args, kwargs, 2, "context")

    def _before(self, args, kwargs):
        log.info("[awslambdaric][handler] _before was called")
        self._set_context(args, kwargs)

    def _after(self):
        log.info("[awslambdaric][handler] _after was called")
        pass

def get_version():
    # type: () -> str
    try:
        import awslambdaric
        return getattr(awslambdaric, "__version__", "")
    except ImportError:
        return ""

def patch():
    """
    Patch the AWS Lambda Runtime Interface Client to enable tracing.
    """
    import awslambdaric
    
    if getattr(awslambdaric, "__datadog_patch", False):
        return

    Pin().onto(awslambdaric)

    wrap(awslambdaric, "bootstrap.handle_event_request", _wrap_handle_event_request)

    config._awslambdaric_patched = True


def unpatch():
    """
    Remove AWS Lambda Runtime Interface Client tracing.
    """
    import awslambdaric
    
    if getattr(awslambdaric, "__datadog_patch", False):
        return
    
    awslambdaric.__datadog_patch = False

@with_traced_module
def _wrap_handle_event_request(awslambdaric, pin, func, instance, args, kwargs):
    """
    Wrap the handle_event_request method.
    """
    _invoke_id = get_argument_value(args, kwargs, 2, "invoke_id")
    _event_body = get_argument_value(args, kwargs, 3, "event_body")
    log.info("[awslambdaric] handle_event_request was called with invoke_id: %s, event_body: %s", _invoke_id, _event_body)

    _customer_handler = get_argument_value(args, kwargs, 1, "request_handler")
    _datadog_handler = DatadogInstrumentation()

    wrap(_customer_handler, _datadog_handler)

    func(*args, **kwargs)



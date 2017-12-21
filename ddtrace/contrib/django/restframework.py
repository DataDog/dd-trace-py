import logging
log = logging.getLogger(__name__)

from rest_framework.views import APIView

ORIGINAL_HANDLE_EXCEPTION = '_datadog_original_handle_exception'


def patch_rest_framework(tracer):
    """ Patches rest_framework app.

    To trace exceptions occuring during view processing we currently use a TraceExceptionMiddleware.
    However the rest_framework handles exceptions before they come to our middleware.
    So we need to manually patch the rest_framework exception handler
    to set the exception stack trace in the current span.

    """

    # do not patch if already patched
    if hasattr(APIView, ORIGINAL_HANDLE_EXCEPTION):
        log.debug("rest_framework is already patched")
        return

    # save original handle_exception method to another namespace
    setattr(APIView, ORIGINAL_HANDLE_EXCEPTION, APIView.handle_exception)

    def traced_handle_exception(self, exc):
        """ Sets the error message, error type and exception stack trace to the current span
            before calling the original exception handler.
        """
        span = tracer.current_span()
        if span is not None:
            span.set_traceback()

        original_handle_exception = getattr(self, ORIGINAL_HANDLE_EXCEPTION)
        return original_handle_exception(exc)

    # use the traced exception handler
    setattr(APIView, 'handle_exception', traced_handle_exception)

def unpatch_rest_framework():
    if hasattr(APIView, ORIGINAL_HANDLE_EXCEPTION):
        original_handle_exception = getattr(APIView, ORIGINAL_HANDLE_EXCEPTION)
        setattr(APIView, 'handle_exception', original_handle_exception)
        delattr(APIView, ORIGINAL_HANDLE_EXCEPTION)
    else:
        log.debug("rest_framework is not patched")

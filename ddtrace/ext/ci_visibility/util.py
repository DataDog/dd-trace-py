from ddtrace.internal.logger import get_logger

log = get_logger(__name__)

def _catch_and_log_exceptions():
    """This decorator is meant to be used around all methods of the CIVisibility classes.

    No uncaught errors should ever reach the integration-side, and potentially cause crashes.
    """
    def wrapper(func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            log.error("Uncaught exception occurred while calling %s: %s", func.__name__, exc_info=True)

    return wrapper
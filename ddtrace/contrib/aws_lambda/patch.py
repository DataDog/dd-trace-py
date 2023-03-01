from importlib import import_module
import os
import signal

from ddtrace import tracer
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.logger import get_logger
from ddtrace.internal.serverless import in_aws_lambda
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


log = get_logger(__name__)


def _crash_flush(_, __):
    """
    Tags the current root span with an Impending Timeout error.
    Finishes spans with ancestors from the current span.
    """

    root_span = tracer.current_root_span()
    root_span.error = 1
    root_span.set_tag_str(ERROR_MSG, "Datadog detected an Impending Timeout")
    root_span.set_tag_str(ERROR_TYPE, "Impending Timeout")

    current_span = tracer.current_span()
    current_span.finish_with_ancestors()


def _handle_signal(sig, f):
    """
    Returns a signal of type `sig` with function `f`, if there are
    no previously defined signals.

    Else, wraps the given signal with the previously defined one,
    so no signals are overridden.
    """
    old_signal = signal.getsignal(sig)
    if not callable(old_signal) or old_signal == f:
        return signal.signal(sig, f)

    def wrap_signals(*args, **kwargs):
        if old_signal is not None:
            old_signal(*args, **kwargs)
        f(*args, **kwargs)

    return signal.signal(sig, wrap_signals)


def _check_timeout(context):
    """
    Creates a timeout to detect when an AWS Lambda handler's remaining
    time is about to end.

    Crashes flushes when the signal is activated.
    """
    _handle_signal(signal.SIGALRM, _crash_flush)
    remaining_time_in_millis = context.get_remaining_time_in_millis()
    apm_flush_deadline = int(os.environ.get("DD_APM_FLUSH_DEADLINE_MILLISECONDS", 0))

    if apm_flush_deadline > 0 and apm_flush_deadline <= remaining_time_in_millis:
        if apm_flush_deadline < 200:
            log.warning(
                "DD_APM_FLUSH_DEADLINE_MILLISECONDS will be overridden to 200ms.",
                "The value before was %d, more time for span flushing was needed.",
                apm_flush_deadline,
            )

            # A minimum deadline of 200ms is set to allow us to have at
            # least 100ms to flush our span queue.
            apm_flush_deadline = 200

        remaining_time_in_millis = apm_flush_deadline

    # Subtracting 100ms to ensure we have time to flush.
    # TODO: Update logic to calculate an approximate of how long it will
    # take us to flush the spans on the queue.
    remaining_time_in_seconds = max((remaining_time_in_millis - 100) / 1000, 0)
    signal.setitimer(signal.ITIMER_REAL, remaining_time_in_seconds)


def _datadog_instrumentation(func, args, kwargs):
    """Patches an AWS Lambda handler function for Datadog instrumentation."""
    context = get_argument_value(args, kwargs, -1, "context")  # context is always the last parameter
    _check_timeout(context)

    return func(*args, **kwargs)


def _modify_module_name(module_name):
    """Returns a valid modified module to get imported."""
    return ".".join(module_name.split("/"))


def _get_handler_and_module():
    """Returns the user AWS Lambda handler and module."""
    path = os.environ.get("DD_LAMBDA_HANDLER", None)
    if path is None:
        from datadog_lambda.wrapper import datadog_lambda_wrapper

        handler = getattr(datadog_lambda_wrapper, "__call__")

        def wrapper(func, args, kwargs):
            return _datadog_instrumentation(func, args, kwargs)

        return handler, datadog_lambda_wrapper, wrapper
    else:
        parts = path.rsplit(".", 1)
        (mod_name, handler_name) = parts
        modified_mod_name = _modify_module_name(mod_name)
        handler_module = import_module(modified_mod_name)
        handler = getattr(handler_module, handler_name)
        return handler, handler_module, _datadog_instrumentation


def _has_patch_module():
    """
    Ensures that the `aws_lambda` integration can be patched.

    It checks either the user has the DD_LAMBDA_HANDLER set correctly.
    Or if the `datadog_lambda` package is installed.
    """
    path = os.environ.get("DD_LAMBDA_HANDLER", None)
    if path is None:
        try:
            import_module("datadog_lambda.wrapper")
        except Exception:
            return False
    else:
        parts = path.rsplit(".", 1)
        if len(parts) != 2:
            return False
    return True


def patch():
    """Patches an AWS Lambda using the `datadog-lambda-py` Lambda layer."""

    # It's expected to only patch only in AWS Lambda environments.
    # The need to check if a patch module exists is to avoid patching
    # when `ddtrace` is present but not `datadog-lambda`.
    if not in_aws_lambda() and not _has_patch_module():
        return

    handler, handler_module, wrapper = _get_handler_and_module()

    if getattr(handler_module, "_datadog_patch", False):
        return
    setattr(handler_module, "_datadog_patch", True)

    wrap(handler, wrapper)


def unpatch():
    if not in_aws_lambda() and not _has_patch_module():
        return

    handler, handler_module, wrapper = _get_handler_and_module()

    if not getattr(handler_module, "_datadog_patch", False):
        return
    setattr(handler_module, "_datadog_patch", False)

    unwrap(handler, wrapper)

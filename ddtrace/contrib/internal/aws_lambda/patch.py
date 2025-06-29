from importlib import import_module
import signal
from typing import Dict

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.internal.aws_lambda._cold_start import is_cold_start
from ddtrace.contrib.internal.aws_lambda._cold_start import set_cold_start
from ddtrace.internal.logger import get_logger
from ddtrace.internal.serverless import in_aws_lambda
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap
from ddtrace.settings._config import _get_config
from ddtrace.trace import tracer


def get_version():
    # type: () -> str
    return ""


def _supported_versions() -> Dict[str, str]:
    return {"datadog_lambda": "*"}


class DDLambdaLogger:
    """Uses `DDLogger` to log only on cold start invocations."""

    def __init__(self):
        self.logger = get_logger(__name__)
        self.is_cold_start = is_cold_start()

    def exception(self, msg, *args, exc_info=True, **kwargs):
        if self.is_cold_start:
            self.logger.error(msg, *args, exc_info=exc_info, **kwargs)

    def warning(self, msg, *args, **kwargs):
        if self.is_cold_start:
            self.logger.warning(msg, *args, **kwargs)


log = DDLambdaLogger()


class TimeoutChannel:
    def __init__(self, context):
        self.crashed = False
        self.context = context

    def _handle_signal(self, sig, f):
        """
        Returns a signal of type `sig` with function `f`, if there are
        no previously defined signals.

        Else, wraps the given signal with the previously defined one,
        so no signals are overridden.
        """
        old_signal = signal.getsignal(sig)

        def wrap_signals(*args, **kwargs):
            if old_signal is not None:
                old_signal(*args, **kwargs)
            f(*args, **kwargs)

        # Return the incoming signal if any of the following cases happens:
        # - old signal does not exist,
        # - old signal is the same as the incoming, or
        # - old signal is our wrapper.
        # This avoids multiple signal calling and infinite wrapping.
        if not callable(old_signal) or old_signal == f or old_signal == wrap_signals:
            return signal.signal(sig, f)

        return signal.signal(sig, wrap_signals)

    def _start(self):
        self._handle_signal(signal.SIGALRM, self._crash_flush)

        remaining_time_in_millis = self.context.get_remaining_time_in_millis()
        apm_flush_deadline = _get_config("DD_APM_FLUSH_DEADLINE_MILLISECONDS", 100, int)
        apm_flush_deadline = 100 if apm_flush_deadline < 0 else apm_flush_deadline

        # TODO: Update logic to calculate an approximate of how long it will
        # take us to flush the spans on the queue.
        remaining_time_in_seconds = max(((remaining_time_in_millis - apm_flush_deadline) / 1000), 0)
        signal.setitimer(signal.ITIMER_REAL, remaining_time_in_seconds)

    def _crash_flush(self, _, __):
        """
        Tags the current root span with an Impending Timeout error.
        Finishes spans with ancestors from the current span.
        """
        self._remove_alarm_signal()
        self.crashed = True

        root_span = tracer.current_root_span()
        if root_span is not None:
            root_span.error = 1
            root_span.set_tag_str(ERROR_MSG, "Datadog detected an Impending Timeout")
            root_span.set_tag_str(ERROR_TYPE, "Impending Timeout")
        else:
            log.warning("An impending timeout was reached, but no root span was found. No error will be tagged.")

        current_span = tracer.current_span()
        if current_span is not None:
            current_span.finish_with_ancestors()

    def _remove_alarm_signal(self):
        """Removes the handler set for the signal `SIGALRM`."""
        signal.alarm(0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)

    def stop(self):
        self._remove_alarm_signal()


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
        set_cold_start()
        self._set_context(args, kwargs)
        self.timeoutChannel = TimeoutChannel(self.context)

        self.timeoutChannel._start()

    def _after(self):
        if not self.timeoutChannel.crashed:
            self.timeoutChannel.stop()


def _modify_module_name(module_name):
    """Returns a valid modified module to get imported."""
    return ".".join(module_name.split("/"))


def _get_handler_and_module():
    """Returns the user AWS Lambda handler and module."""
    path = _get_config("DD_LAMBDA_HANDLER", None, str)
    _datadog_instrumentation = DatadogInstrumentation()

    if path is None:
        from datadog_lambda.wrapper import datadog_lambda_wrapper

        wrapper_module = datadog_lambda_wrapper
        wrapper_handler = datadog_lambda_wrapper.__call__

        return wrapper_handler, wrapper_module, _datadog_instrumentation
    else:
        parts = path.rsplit(".", 1)
        (mod_name, handler_name) = parts
        modified_mod_name = _modify_module_name(mod_name)
        handler_module = import_module(modified_mod_name)
        handler = getattr(handler_module, handler_name)

        if callable(handler):
            class_name = type(handler).__name__
            is_function = not isinstance(handler, type) and hasattr(handler, "__code__") and class_name == "function"
            # handler is a function
            #
            # note: this is a best effort to identify function based handlers
            # this will not cover all cases
            if is_function:
                return handler, handler_module, _datadog_instrumentation

            # handler must be either a class or an instance of a class
            #
            # note: if handler is a class instance with `__code__` defined,
            # we will prioritize the `__call__` method, ignoring `__code__`.
            class_module = getattr(handler_module, class_name)
            class_handler = class_module.__call__

            if isinstance(handler, type):
                # class handler is a metaclass
                if hasattr(class_handler, "__func__"):
                    class_handler = class_handler.__func__

            return class_handler, class_module, _datadog_instrumentation
        else:
            raise TypeError("Handler type is not supported to patch.")


def _has_patch_module():
    """
    Ensures that the `aws_lambda` integration can be patched.

    It checks either the user has the DD_LAMBDA_HANDLER set correctly.
    Or if the `datadog_lambda` package is installed.
    """
    path = _get_config("DD_LAMBDA_HANDLER", None, str)
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

    try:
        handler, handler_module, wrapper = _get_handler_and_module()

        if getattr(handler_module, "_datadog_patch", False):
            return

        wrap(handler, wrapper)

        handler_module._datadog_patch = True
    except AttributeError:
        # User code might contain `ddtrace.patch_all()` or `ddtrace.patch(aws_lambda=True)`
        # which might cause a circular dependency. Skipping.
        return
    except Exception:
        log.exception("Error patching handler. Timeout spans will not be generated.")

        return


def unpatch():
    if not in_aws_lambda() and not _has_patch_module():
        return

    try:
        handler, handler_module, wrapper = _get_handler_and_module()

        if not getattr(handler_module, "_datadog_patch", False):
            return

        unwrap(handler, wrapper)

        handler_module._datadog_patch = False
    except AttributeError:
        return
    except Exception:
        log.exception("Error unpatching handler.")

        return

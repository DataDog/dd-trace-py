"""Public API for User events"""
from functools import wraps

from ddtrace.appsec import _metrics
from ddtrace.appsec._trace_utils import block_request  # noqa: F401
from ddtrace.appsec._trace_utils import block_request_if_user_blocked  # noqa: F401
from ddtrace.appsec._trace_utils import should_block_user  # noqa: F401
from ddtrace.appsec._trace_utils import track_custom_event
from ddtrace.appsec._trace_utils import track_user_login_failure_event
from ddtrace.appsec._trace_utils import track_user_login_success_event
from ddtrace.appsec._trace_utils import track_user_signup_event
import ddtrace.internal.core


ddtrace.internal.core.on("set_user_for_asm", block_request_if_user_blocked, "block_user")


def _telemetry_report_factory(event_name: str):
    """
    Factory function to create a telemetry report decorator.
    This decorator will report the event name when the decorated function is called.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _metrics._report_ato_sdk_usage(event_name, False)
            return func(*args, **kwargs)

        return wrapper

    return decorator


track_custom_event = _telemetry_report_factory("custom")(track_custom_event)
track_user_login_success_event = _telemetry_report_factory("login_success")(track_user_login_success_event)
track_user_login_failure_event = _telemetry_report_factory("login_failure")(track_user_login_failure_event)
track_user_signup_event = _telemetry_report_factory("signup")(track_user_signup_event)

"""
Public User Tracking SDK Version 2

This module provides a public interface for tracking user events.
This replaces the previous version of the SDK available in ddtrace.appsec.trace_utils
Implementation can change in the future, but the interface will remain compatible.
"""

import typing as t

from ddtrace import tracer as _tracer
from ddtrace.appsec import _asm_request_context
from ddtrace.appsec import _constants
from ddtrace.appsec import _trace_utils


def track_login_success(login: str, user_id: t.Any = None, metadata: t.Optional[t.Dict[str, t.Any]] = None) -> None:
    """
    Track a successful user login event.

    This function should be called when a user successfully logs in to the application.
    It will create an event that can be used for monitoring and analysis.
    """
    _trace_utils.track_user_login_success_event(_tracer, user_id, login=login, metadata=metadata)


def track_login_failure(login: str, exists: bool, metadata: t.Optional[t.Dict[str, t.Any]] = None):
    """
    Track a failed user login event.

    This function should be called when a user fails to log in to the application.
    It will create an event that can be used for monitoring and analysis.
    """
    _trace_utils.track_user_login_failure_event(_tracer, None, exists=exists, login=login, metadata=metadata)


def track_signup(
    login: str, user_id: t.Any = None, success: bool = True, metadata: t.Optional[t.Dict[str, t.Any]] = None
):
    """
    Track a user signup event.

    This function should be called when a user successfully signs up for the application.
    It will create an event that can be used for monitoring and analysis.
    """
    _trace_utils.track_user_signup_event(_tracer, user_id, success, login=login)
    if metadata:
        _trace_utils.track_custom_event(_tracer, "signup_sdk", metadata=metadata)


def track_user(
    login: str, user_id: t.Any = None, session=t.Optional[str], metadata: t.Optional[t.Dict[str, t.Any]] = None
):
    """
    Track an authenticated user.

    This function should be called when a user is authenticated in the application."
    """
    span = _tracer.current_root_span()
    if span is None:
        return
    if user_id:
        span.set_tag_str(_constants.APPSEC.USER_LOGIN_USERID, str(user_id))
    if login:
        span.set_tag_str(_constants.APPSEC.USER_LOGIN_USERNAME, str(login))

    _trace_utils.set_user(_tracer, user_id)
    if metadata:
        _trace_utils.track_custom_event(_tracer, "auth_sdk", metadata=metadata)
    if _asm_request_context.in_asm_context():
        custom_data = {
            "REQUEST_USER_ID": str(user_id) if user_id else None,
            "REQUEST_USERNAME": login,
            "LOGIN_SUCCESS": "sdk",
        }
        if session:
            custom_data["REQUEST_SESSION_ID"] = session
        res = _asm_request_context.call_waf_callback(custom_data=custom_data, force_sent=True)


def track_custom_event(event_name: str, metadata: t.Dict[str, t.Any]):
    """
    Track a custom user event.

    This function should be called when a custom user event occurs in the application.
    It will create an event that can be used for monitoring and analysis.
    """
    _trace_utils.track_custom_event(_tracer, event_name, metadata=metadata)

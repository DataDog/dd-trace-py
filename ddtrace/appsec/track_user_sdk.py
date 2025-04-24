"""
Public User Tracking SDK Version 2

This module provides a public interface for tracking user events.
This replaces the previous version of the SDK available in ddtrace.appsec.trace_utils
Implementation can change in the future, but the interface will remain compatible.
"""

import typing as t

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec import _constants
from ddtrace.appsec import _trace_utils
from ddtrace.appsec._asm_request_context import get_blocked as _get_blocked
from ddtrace.appsec._constants import WAF_ACTIONS as _WAF_ACTIONS
from ddtrace.internal import core as _core
from ddtrace.internal._exceptions import BlockingException


def track_login_success(login: str, user_id: t.Any = None, metadata: t.Optional[t.Dict[str, t.Any]] = None) -> None:
    """
    Track a successful user login event.

    This function should be called when a user successfully logs in to the application.
    It will create an event that can be used for monitoring and analysis.
    """
    _trace_utils.track_user_login_success_event(None, user_id, login=login, metadata=metadata)


def track_login_failure(
    login: str, exists: bool, user_id: t.Any = None, metadata: t.Optional[t.Dict[str, t.Any]] = None
):
    """
    Track a failed user login event.

    This function should be called when a user fails to log in to the application.
    It will create an event that can be used for monitoring and analysis.
    """
    _trace_utils.track_user_login_failure_event(None, user_id, exists=exists, login=login, metadata=metadata)


def track_signup(
    login: str, user_id: t.Any = None, success: bool = True, metadata: t.Optional[t.Dict[str, t.Any]] = None
):
    """
    Track a user signup event.

    This function should be called when a user successfully signs up for the application.
    It will create an event that can be used for monitoring and analysis.
    """
    _trace_utils.track_user_signup_event(None, user_id, success, login=login)
    if metadata:
        _trace_utils.track_custom_event(None, "signup_sdk", metadata=metadata)


def track_user(
    login: str, user_id: t.Any = None, session_id=t.Optional[str], metadata: t.Optional[t.Dict[str, t.Any]] = None
):
    """
    Track an authenticated user.

    This function should be called when a user is authenticated in the application."
    """
    span = _core.get_root_span()
    if span is None:
        return
    if user_id:
        span.set_tag_str(_constants.APPSEC.USER_LOGIN_USERID, str(user_id))
    if login:
        span.set_tag_str(_constants.APPSEC.USER_LOGIN_USERNAME, str(login))

    _trace_utils.set_user(None, user_id, session_id=session_id, may_block=False)
    if metadata:
        _trace_utils.track_custom_event(None, "auth_sdk", metadata=metadata)
    span.set_tag_str(_constants.APPSEC.AUTO_LOGIN_EVENTS_COLLECTION_MODE, _constants.LOGIN_EVENTS_MODE.SDK)
    if _asm_request_context.in_asm_context():
        custom_data = {
            "REQUEST_USER_ID": str(user_id) if user_id else None,
            "REQUEST_USERNAME": login,
            "LOGIN_SUCCESS": "sdk",
        }
        if session_id:
            custom_data["REQUEST_SESSION_ID"] = session_id
        res = _asm_request_context.call_waf_callback(custom_data=custom_data, force_sent=True)
        if res and any(action in [_WAF_ACTIONS.BLOCK_ACTION, _WAF_ACTIONS.REDIRECT_ACTION] for action in res.actions):
            raise BlockingException(_get_blocked())


def track_custom_event(event_name: str, metadata: t.Dict[str, t.Any]):
    """
    Track a custom user event.

    This function should be called when a custom user event occurs in the application.
    It will create an event that can be used for monitoring and analysis.
    """
    _trace_utils.track_custom_event(None, event_name, metadata=metadata)

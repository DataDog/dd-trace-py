"""
Public User Tracking SDK Version 2

This module provides a public interface for tracking user events.
This replaces the previous version of the SDK available in ddtrace.appsec.trace_utils
"""

import typing as t


def track_login_success(login: str, user_id: t.Any = None, metadata: t.Optional[t.Dict[str, t.Any]] = None):
    """
    Track a successful user login event.

    This function should be called when a user successfully logs in to the application.
    It will create an event that can be used for monitoring and analysis.
    """
    pass


def track_login_failure(login: str, exists: bool, metadata: t.Optional[t.Dict[str, t.Any]] = None):
    """
    Track a failed user login event.

    This function should be called when a user fails to log in to the application.
    It will create an event that can be used for monitoring and analysis.
    """
    pass


def track_signup(login: str, user_id: t.Any = None, metadata: t.Optional[t.Dict[str, t.Any]] = None):
    """
    Track a user signup event.

    This function should be called when a user successfully signs up for the application.
    It will create an event that can be used for monitoring and analysis.
    """
    pass


def track_user(login: str, metadata: t.Optional[t.Dict[str, t.Any]] = None):
    """
    Track an authenticated user.

    This function should be called when a user is authenticated in the application."
    """
    pass


def track_custom_event(event_name: str, metadata: t.Optional[t.Dict[str, t.Any]] = None):
    """
    Track a custom user event.

    This function should be called when a custom user event occurs in the application.
    It will create an event that can be used for monitoring and analysis.
    """
    pass

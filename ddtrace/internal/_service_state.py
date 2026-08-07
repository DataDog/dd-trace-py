"""Shared, dependency-free holder for service-resolution facts.

ddtrace.internal.settings._config computes whether the service name was
explicitly provided by the user (as opposed to auto-detected/defaulted).
ddtrace.internal.process_tags needs that same fact to build its tags, but
importing _config from there would recreate the
process_tags -> _config -> telemetry -> process_tags circular import
(_config depends on ddtrace.internal.telemetry for configuration lookups,
and telemetry depends on process_tags for its payload data).

This module lives directly under ddtrace.internal (rather than
ddtrace.internal.settings) and has no dependencies of its own, so it can sit
underneath both without closing that loop: _config.py sets the value once
during Config construction, and process_tags reads it directly.
"""

from typing import Optional


_is_user_provided_service: Optional[bool] = None


def set_is_user_provided_service(value: bool) -> None:
    global _is_user_provided_service
    _is_user_provided_service = value


def is_user_provided_service() -> bool:
    return bool(_is_user_provided_service)

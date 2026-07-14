"""Shared, dependency-free holder for service-resolution facts.

ddtrace.internal.settings._config computes whether the service name was
explicitly provided by the user (as opposed to auto-detected/defaulted).
ddtrace.internal.process_tags needs that same fact to build its tags, but
importing _config from there would recreate the
process_tags -> _config -> telemetry -> process_tags circular import
(_config depends on ddtrace.internal.telemetry for configuration lookups,
and telemetry depends on process_tags for its payload data).

This module lives directly under ddtrace.internal (rather than
ddtrace.internal.settings) and depends only on ddtrace.internal.settings.env (a
foundational, dependency-free env-var shim that even ddtrace's logger builds
on), so it can sit underneath both without closing that loop: _config.py sets
the value once during Config construction, and process_tags reads it directly.
"""

from typing import Optional

from ddtrace.internal.settings import env


def _service_provided_via_env() -> bool:
    """Best-effort read of whether a service name is set in the environment.

    ``Config`` is the authoritative source (``set_is_user_provided_service`` below), but the
    telemetry worker — and therefore process-tag computation — is built very early during
    bootstrap, before ``Config`` is constructed. Process tags are then cached, so reading a
    stale ``False`` here would bake ``svc.auto`` into a user-provided service. Seed the value
    from the environment (the sources ``Config.service`` resolves: ``DD_SERVICE``,
    ``OTEL_SERVICE_NAME``, and a ``service`` tag in ``DD_TAGS``) so the early read is correct;
    ``Config`` still overrides it with the fully-resolved value (incl. stable config).
    """
    if env.get("DD_SERVICE") or env.get("OTEL_SERVICE_NAME"):
        return True
    for pair in env.get("DD_TAGS", "").replace(" ", ",").split(","):
        key, sep, value = pair.partition(":")
        if sep and key.strip() == "service" and value.strip():
            return True
    return False


_is_user_provided_service: Optional[bool] = _service_provided_via_env()


def set_is_user_provided_service(value: bool) -> None:
    global _is_user_provided_service
    _is_user_provided_service = value


def is_user_provided_service() -> bool:
    return bool(_is_user_provided_service)

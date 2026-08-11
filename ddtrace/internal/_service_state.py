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
foundational, dependency-free env-var shim that even ddtrace's logger builds on)
and ddtrace.internal.settings._core (which only reaches the native extension,
env, and the supported-configurations registry — none of which re-enter
process_tags/telemetry/_config), so it can sit underneath both without closing
that loop: _config.py sets the value once during Config construction, and
process_tags reads it directly.
"""

from typing import Mapping
from typing import Optional

from ddtrace.internal.settings import env
from ddtrace.internal.settings._core import FLEET_CONFIG
from ddtrace.internal.settings._core import LOCAL_CONFIG


def _service_in_source(source: Mapping[str, str]) -> bool:
    """Whether a name->value config mapping carries a user-provided service name.

    Mirrors the signals ``Config.service`` resolves from: ``DD_SERVICE``, ``OTEL_SERVICE_NAME``,
    and a ``service`` tag inside ``DD_TAGS``.
    """
    if source.get("DD_SERVICE") or source.get("OTEL_SERVICE_NAME"):
        return True
    for pair in source.get("DD_TAGS", "").replace(" ", ",").split(","):
        key, sep, value = pair.partition(":")
        if sep and key.strip() == "service" and value.strip():
            return True
    return False


def _service_provided_early() -> bool:
    """Best-effort read of whether a service name is user-provided, before Config exists.

    ``Config`` is the authoritative source (``set_is_user_provided_service`` below), but the
    telemetry worker — and therefore process-tag computation — is built very early during
    bootstrap, before ``Config`` is constructed. Process tags are then cached, so reading a
    stale ``False`` here would bake ``svc.auto`` into a user-provided service. Seed from every
    source ``Config.service`` consults — environment variables and local/fleet stable config —
    so the early read is correct even when the service name comes only from stable config;
    ``Config`` still overrides it with the fully-resolved value.
    """
    return any(_service_in_source(source) for source in (env, FLEET_CONFIG, LOCAL_CONFIG))


_is_user_provided_service: Optional[bool] = _service_provided_early()


def set_is_user_provided_service(value: bool) -> None:
    global _is_user_provided_service
    _is_user_provided_service = value


def is_user_provided_service() -> bool:
    return bool(_is_user_provided_service)

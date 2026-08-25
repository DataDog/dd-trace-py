"""Utilities for Google Cloud Pub/Sub instrumentation."""

from ddtrace.internal.settings import env
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.settings._config import config
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.internal.utils.formats import asbool


_DEPRECATED_SPAN_LINKS_ENV = "DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_AS_SPAN_LINKS"


def _propagation_as_span_links_enabled() -> bool:
    if "google_cloud_pubsub" in config._propagation_as_span_links:
        return True
    if _DEPRECATED_SPAN_LINKS_ENV in env:
        deprecate(
            f"{_DEPRECATED_SPAN_LINKS_ENV} is deprecated",
            message="Use DD_TRACE_PROPAGATION_AS_SPAN_LINKS with a comma-separated list of "
            "integration names (e.g. 'google_cloud_pubsub,kafka') instead.",
            removal_version="5.0.0",
            category=DDTraceDeprecationWarning,
        )
        return asbool(_get_config(_DEPRECATED_SPAN_LINKS_ENV, default=False))
    return False


def ensure_config_registered() -> None:
    """Register the google_cloud_pubsub integration config if not already present.

    Called from both patch.py (pull subscriptions) and trace_handlers.py (push subscriptions)
    so the config is available regardless of whether google-cloud-pubsub is installed.
    """
    if "google_cloud_pubsub" in config._integration_configs:
        return
    config._add(
        "google_cloud_pubsub",
        dict(
            distributed_tracing_enabled=asbool(_get_config("DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_ENABLED", default=True)),
            propagation_as_span_links=_propagation_as_span_links_enabled(),
        ),
    )


def parse_resource_path(path: object) -> tuple[str, str]:
    """Parse a GCP resource path into (project_id, resource_id)."""
    if not isinstance(path, str):
        return "", ""
    parts = path.split("/")
    project_id = parts[1] if len(parts) >= 2 else ""
    resource_id = parts[3] if len(parts) >= 4 else path
    return project_id, resource_id

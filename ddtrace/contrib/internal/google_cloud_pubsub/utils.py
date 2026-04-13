"""Utilities for Google Cloud Pub/Sub instrumentation."""

from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.settings._config import config
from ddtrace.internal.utils.formats import asbool


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
            propagation_as_span_links=asbool(
                _get_config("DD_GOOGLE_CLOUD_PUBSUB_PROPAGATION_AS_SPAN_LINKS", default=False)
            ),
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

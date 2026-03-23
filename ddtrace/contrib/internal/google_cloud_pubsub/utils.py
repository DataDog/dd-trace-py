"""Utilities for Google Cloud Pub/Sub instrumentation."""

from ddtrace import config
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_MESSAGE_ID
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.utils.formats import asbool


def ensure_config_registered():
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


def parse_resource_path(path):
    """Parse a GCP resource path into (project_id, resource_id)."""
    if not isinstance(path, str):
        return "", ""
    parts = path.split("/")
    project_id = parts[1] if len(parts) >= 2 else ""
    resource_id = parts[3] if len(parts) >= 4 else path
    return project_id, resource_id


def set_pubsub_receive_attributes(span, project_id, subscription_id, message_id=None):
    """Set common span attributes for Pub/Sub receive operations (pull and push)."""
    span._set_attribute(COMPONENT, config.google_cloud_pubsub.integration_name)
    span._set_attribute(SPAN_KIND, SpanKind.CONSUMER)
    span._set_attribute("gcloud.project_id", project_id)
    span._set_attribute(MESSAGING_SYSTEM, "pubsub")
    span._set_attribute(MESSAGING_DESTINATION_NAME, subscription_id)
    span._set_attribute(MESSAGING_OPERATION, "receive")
    span._set_attribute(_SPAN_MEASURED_KEY, 1)
    if message_id:
        span._set_attribute(MESSAGING_MESSAGE_ID, message_id)

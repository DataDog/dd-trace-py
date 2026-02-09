"""Remote Configuration handler for SCA detection.

This module manages the subscription to Remote Configuration for receiving
lists of functions to instrument for vulnerability detection.
"""

from typing import Sequence

from ddtrace.appsec._constants import SCA
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


log = get_logger(__name__)


class SCADetectionRC(PubSub):
    """PubSub for SCA detection remote configuration.

    This class manages the publisher-subscriber pattern for receiving
    SCA detection configuration updates from the remote configuration backend.
    """

    __subscriber_class__ = RemoteConfigSubscriber
    __publisher_class__ = RemoteConfigPublisher
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, callback):
        """Initialize SCA detection RC with callback.

        Args:
            callback: Function to call when RC payload is received
        """
        self._publisher = self.__publisher_class__(self.__shared_data__)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "SCA_DETECTION")


def _sca_detection_callback(payload_list: Sequence) -> None:
    """Process SCA detection RC payloads.

    This callback is invoked when the RC backend sends updated target lists.
    It processes additions and removals of instrumentation targets.

    Payload structure:
    {
        "targets": [
            "module.submodule:function",
            "package.module:Class.method"
        ]
    }

    Args:
        payload_list: List of Payload objects from Remote Configuration
    """
    try:
        from ddtrace.appsec.sca._instrumenter import apply_instrumentation_updates

        if not payload_list:
            log.debug("Received empty SCA detection payload list")
            return

        targets_to_add = []
        targets_to_remove = []

        for payload in payload_list:
            payload_path = getattr(payload, "path", "<unknown>")
            try:
                log.debug(
                    "Processing SCA detection payload: path=%s, content=%s",
                    payload_path,
                    bool(getattr(payload, "content", None)),
                )

                # Get content and metadata safely
                content = getattr(payload, "content", None)
                metadata = getattr(payload, "metadata", {})

                if content is None:
                    # Deletion - remove instrumentation
                    # When content is None, it means this target file was deleted
                    if isinstance(metadata, dict) and "targets" in metadata:
                        targets = metadata["targets"]
                        if isinstance(targets, list):
                            targets_to_remove.extend(targets)
                            log.debug("Marked %d targets for removal", len(targets))
                else:
                    # Addition/Update
                    if isinstance(content, dict):
                        targets = content.get("targets", [])
                        if isinstance(targets, list):
                            targets_to_add.extend(targets)
                            log.debug("Marked %d targets for addition", len(targets))
                        else:
                            log.warning("Invalid targets format in payload (not a list): %s", type(targets))
                    else:
                        log.warning("Invalid content format in payload (not a dict): %s", type(content))

            except Exception as e:
                # Don't let one bad payload stop processing others
                # Log which specific payload failed for debugging
                log.error("Failed to process SCA detection payload (path=%s): %s", payload_path, e, exc_info=True)
                # Send telemetry for payload processing failures
                telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE.APPSEC, "sca.payload_errors", 1, tags=(("payload_path", payload_path),)
                )
                continue

        # Apply instrumentation updates
        if targets_to_add or targets_to_remove:
            log.info(
                "Applying SCA instrumentation updates: %d to add, %d to remove",
                len(targets_to_add),
                len(targets_to_remove),
            )
            try:
                apply_instrumentation_updates(targets_to_add, targets_to_remove)
            except Exception:
                log.error("Failed to apply SCA instrumentation updates", exc_info=True)
        else:
            log.debug("No instrumentation updates to apply")

    except Exception:
        # Catch-all to prevent RC processing from dying
        log.error("Fatal error in SCA detection callback", exc_info=True)


def enable_sca_detection_rc() -> None:
    """Register SCA detection with Remote Configuration.

    This function subscribes to the SCA_DETECTION product in Remote Configuration,
    which will deliver lists of functions to instrument for vulnerability detection.
    """
    log.debug("Registering SCA detection with Remote Configuration")

    # Create PubSub instance with callback
    sca_rc = SCADetectionRC(_sca_detection_callback)

    # Register with the RC poller
    # restart_on_fork=True ensures the subscriber thread is restarted in child processes
    remoteconfig_poller.register(SCA.RC_PRODUCT, sca_rc, restart_on_fork=True)

    log.info("SCA detection RC enabled (product: %s)", SCA.RC_PRODUCT)


def disable_sca_detection_rc() -> None:
    """Unregister SCA detection from Remote Configuration.

    This function unsubscribes from the SCA_DETECTION product,
    stopping the reception of instrumentation target updates.
    """
    log.debug("Unregistering SCA detection from Remote Configuration")

    remoteconfig_poller.unregister(SCA.RC_PRODUCT)

    log.info("SCA detection RC disabled")

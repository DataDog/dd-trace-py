"""
FFAndE (Feature Flagging and Experimentation) product implementation.

This product receives feature flag configuration rules from Remote Configuration
and processes them through the native FFAndE processor.
"""
import enum
import json
import typing as t

from ddtrace import config
from ddtrace.internal.ffande._native import process_ffe_configuration
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber


requires = ["remote-configuration"]

log = get_logger(__name__)

# FFAndE product name constant
FFE_FLAGS_PRODUCT = "FFE_FLAGS"


class FFAndECapabilities(enum.IntFlag):
    """FFAndE Remote Configuration capabilities."""

    FFE_FLAG_CONFIGURATION_RULES = 1 << 46


class FFAndEAdapter(PubSub):
    """
    FFAndE Remote Configuration adapter.

    Receives feature flag configuration rules and forwards raw bytes to native processor.
    """

    __publisher_class__ = RemoteConfigPublisher
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self):
        self._publisher = self.__publisher_class__(self.__shared_data__)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, self.rc_callback, FFE_FLAGS_PRODUCT)

        # Register with Remote Configuration poller
        if config._remote_config_enabled:
            from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

            remoteconfig_poller.register(
                FFE_FLAGS_PRODUCT,
                self,
                restart_on_fork=True,
                capabilities=[FFAndECapabilities.FFE_FLAG_CONFIGURATION_RULES],
            )

    def rc_callback(self, payloads: t.Sequence[Payload]) -> None:
        """
        Process FFE configuration payloads from Remote Configuration.

        Args:
            payloads: Sequence of configuration payloads
        """
        for payload in payloads:
            if payload.metadata is None:
                log.debug("Ignoring invalid FFE payload with no metadata, path: %s", payload.path)
                continue

            log.debug("Received FFE config payload: %s", payload.metadata.id)

            if payload.content is None:
                log.debug(
                    "Received FFE config deletion, product: %s, path: %s",
                    payload.metadata.product_name,
                    payload.path,
                )
                # Handle deletion/removal of configuration
                continue

            try:
                # Serialize payload content to bytes for native processing
                # The native function expects raw bytes, so we convert the dict to JSON
                config_json = json.dumps(payload.content, ensure_ascii=False)
                config_bytes = config_json.encode("utf-8")

                log.debug("Processing FFE config ID: %s, size: %d bytes", payload.metadata.id, len(config_bytes))

                success = process_ffe_configuration(config_bytes)
                if success:
                    log.debug("Successfully processed FFE config ID: %s", payload.metadata.id)
                else:
                    log.warning("Failed to process FFE config ID: %s", payload.metadata.id)

            except Exception as e:
                log.error("Error processing FFE config payload: %s", e, exc_info=True)


_adapter: t.Optional[FFAndEAdapter] = None


def post_preload():
    """Called during preload phase."""
    pass


def start():
    """Start the FFAndE product and register with Remote Configuration."""
    global _adapter

    if not config._remote_config_enabled:
        log.debug("Remote configuration disabled, FFAndE not started")
        return

    _adapter = FFAndEAdapter()

    log.debug("FFAndE product registered with Remote Configuration")


def restart(join=False):
    """Restart the FFAndE product."""
    pass


def stop(join=False):
    """Stop the FFAndE product."""
    global _adapter

    if not config._remote_config_enabled:
        return

    from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

    remoteconfig_poller.unregister(FFE_FLAGS_PRODUCT)
    _adapter = None

    log.debug("FFAndE product unregistered from Remote Configuration")


def at_exit(join=False):
    """Called at exit."""
    stop(join=join)

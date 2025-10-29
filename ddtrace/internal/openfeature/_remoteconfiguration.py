"""
FFE (Feature Flagging and Experimentation) product implementation.

This product receives feature flag configuration rules from Remote Configuration
and processes them through the native FFE processor.
"""
import enum
import json
import os
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._ffe_mock import mock_process_ffe_configuration
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller


log = get_logger(__name__)

FFE_FLAGS_PRODUCT = "FFE_FLAGS"


class FFECapabilities(enum.IntFlag):
    """FFE Remote Configuration capabilities."""

    FFE_FLAG_CONFIGURATION_RULES = 1 << 46


class FFEAdapter(PubSub):
    """
    FFE Remote Configuration adapter.

    Receives feature flag configuration rules and forwards raw bytes to native processor.
    """

    __publisher_class__ = RemoteConfigPublisher
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self, callback):
        self._publisher = self.__publisher_class__(self.__shared_data__)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, callback, "FFE_FLAGS")


def featureflag_rc_callback(payloads: t.Sequence[Payload]) -> None:
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
            mock_process_ffe_configuration(payload.content)
            log.debug("Processing FFE config ID: %s, size: %d bytes", payload.metadata.id, len(config_bytes))
        except Exception as e:
            log.debug("Error processing FFE config payload: %s", e, exc_info=True)


def enable_featureflags_rc() -> None:
    log.debug("[%s][P: %s] Register ASM Remote Config Callback", os.getpid(), os.getppid())
    feature_flag_rc = FFEAdapter(featureflag_rc_callback)
    remoteconfig_poller.register(
        FFE_FLAGS_PRODUCT,
        feature_flag_rc,
        restart_on_fork=True,
        capabilities=[FFECapabilities.FFE_FLAG_CONFIGURATION_RULES],
    )


def disable_featureflags_rc() -> None:
    remoteconfig_poller.unregister(FFE_FLAGS_PRODUCT)

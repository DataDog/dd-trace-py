"""
FFE (Feature Flagging and Experimentation) product implementation.

This product receives feature flag configuration rules from Remote Configuration
and processes them through the native FFE processor.
"""

import enum
import os
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller


log = get_logger(__name__)

FFE_FLAGS_PRODUCT = "FFE_FLAGS"


class FFECapabilities(enum.IntFlag):
    """FFE Remote Configuration capabilities."""

    FFE_FLAG_CONFIGURATION_RULES = 1 << 46


class FeatureFlagCallback(RCCallback):
    """Remote Configuration callback for Feature Flagging and Experimentation (FFE)."""

    def __call__(self, payloads: t.Sequence[Payload]) -> None:
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
                process_ffe_configuration(payload.content)
                log.debug("Processing FFE config ID: %s, size: %d bytes", payload.metadata.id, len(payload.content))
            except Exception as e:
                log.debug("Error processing FFE config payload: %s", e, exc_info=True)


# Global callback instance
_featureflag_rc_callback = FeatureFlagCallback()


def enable_featureflags_rc() -> None:
    log.debug("[%s][P: %s] Register FFE Remote Config Callback", os.getpid(), os.getppid())
    remoteconfig_poller.register(
        FFE_FLAGS_PRODUCT,
        _featureflag_rc_callback,
        capabilities=[FFECapabilities.FFE_FLAG_CONFIGURATION_RULES],
    )


def disable_featureflags_rc() -> None:
    remoteconfig_poller.unregister(FFE_FLAGS_PRODUCT)

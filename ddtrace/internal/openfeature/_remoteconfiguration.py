"""
FFE (Feature Flagging and Experimentation) product implementation.

This product receives feature flag configuration rules from Remote Configuration
and processes them through the native FFE processor.
"""

import os
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import RemoteConfigCapabilities
from ddtrace.internal.native import RemoteConfigProduct
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller


log = get_logger(__name__)

FFE_FLAGS_PRODUCT = RemoteConfigProduct.FfeFlags


class FeatureFlagCallback(RCCallback):
    """Remote Configuration callback for Feature Flagging and Experimentation (FFE)."""

    def __init__(self) -> None:
        # AIDEV-NOTE: Path of the configuration currently loaded, so a removal only
        # clears it when it names that same path. Replacing one UFC config with
        # another arrives as remove(old) + add(new), and in a forked child those two
        # do not necessarily arrive together: the native shared-memory distribution
        # publishes a manifest per file operation (see src/native/rc_shm.rs), with
        # the add published during the fetch and the remove right after it. A child
        # whose reader wakes between the two publishes dispatches the add first and
        # the remove second, so an unqualified clear would wipe the configuration
        # that just became current and every later evaluation would report
        # PROVIDER_NOT_READY until the next unrelated config change.
        self._applied_path: t.Optional[str] = None

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
                if self._applied_path is not None and payload.path != self._applied_path:
                    log.debug(
                        "Ignoring FFE config deletion for %s; %s is the applied configuration",
                        payload.path,
                        self._applied_path,
                    )
                    continue
                _set_ffe_config(None)
                self._applied_path = None
                continue

            try:
                if process_ffe_configuration(payload.content):
                    self._applied_path = payload.path
                log.debug("Processing FFE config ID: %s, size: %d bytes", payload.metadata.id, len(payload.content))
            except Exception as e:
                log.debug("Error processing FFE config payload: %s", e, exc_info=True)


# Global callback instance
_featureflag_rc_callback = FeatureFlagCallback()


def enable_featureflags_rc() -> None:
    log.debug("[%s][P: %s] Register FFE Remote Config Callback", os.getpid(), os.getppid())
    remoteconfig_poller.register_callback(
        FFE_FLAGS_PRODUCT,
        _featureflag_rc_callback,
        capabilities=[RemoteConfigCapabilities.FfeFlagConfigurationRules],
    )
    remoteconfig_poller.enable_product(FFE_FLAGS_PRODUCT)


def disable_featureflags_rc() -> None:
    remoteconfig_poller.unregister_callback(FFE_FLAGS_PRODUCT)
    remoteconfig_poller.disable_product(FFE_FLAGS_PRODUCT)

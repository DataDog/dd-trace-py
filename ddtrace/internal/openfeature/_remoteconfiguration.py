"""
FFE (Feature Flagging and Experimentation) product implementation.

This product receives feature flag configuration rules from Remote Configuration
and processes them through the native FFE processor.
"""

import enum
import os
import typing as t

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._config import _set_ffe_config
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

    def __init__(self):
        self._config_state: dict[str, dict] = {}

    def __call__(self, payloads: t.Sequence[Payload]) -> None:
        """
        Process FFE configuration payloads from Remote Configuration.

        Maintains per-path state and merges all UFC configs into a single
        unified configuration after each delta, so multiple configs (e.g.
        from different FFE environments) coexist correctly.
        """
        changed = False
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
                if payload.path in self._config_state:
                    del self._config_state[payload.path]
                    changed = True
                continue

            self._config_state[payload.path] = payload.content
            changed = True

        if not changed:
            return

        merged = self._merge_configurations()
        if merged is not None:
            try:
                process_ffe_configuration(merged)
                log.debug(
                    "Processed merged FFE config, %d flags from %d sources",
                    len(merged["flags"]),
                    len(self._config_state),
                )
            except Exception as e:
                log.debug("Error processing merged FFE config: %s", e, exc_info=True)
        else:
            _set_ffe_config(None)

    def _merge_configurations(self) -> t.Optional[dict]:
        if not self._config_state:
            return None
        configs = list(self._config_state.values())
        merged = dict(configs[0])
        merged_flags: dict = {}
        for config in configs:
            if "flags" in config:
                merged_flags.update(config["flags"])
        merged["flags"] = merged_flags
        return merged


# Global callback instance
_featureflag_rc_callback = FeatureFlagCallback()


def enable_featureflags_rc() -> None:
    log.debug("[%s][P: %s] Register FFE Remote Config Callback", os.getpid(), os.getppid())
    remoteconfig_poller.register_callback(
        FFE_FLAGS_PRODUCT,
        _featureflag_rc_callback,
        capabilities=[FFECapabilities.FFE_FLAG_CONFIGURATION_RULES],
    )
    remoteconfig_poller.enable_product(FFE_FLAGS_PRODUCT)


def disable_featureflags_rc() -> None:
    remoteconfig_poller.unregister_callback(FFE_FLAGS_PRODUCT)
    remoteconfig_poller.disable_product(FFE_FLAGS_PRODUCT)

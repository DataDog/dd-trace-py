"""
FFE (Feature Flagging and Experimentation) product implementation.

This product receives feature flag configuration rules from Remote Configuration
and processes them through the native FFE processor.
"""

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
        # Path of the configuration currently loaded, so a removal only
        # clears it when it names that same path. Replacing a config is remove(old) +
        # add(new), and a forked child can see the two in either order because the
        # shared-memory distribution publishes a manifest per file operation (see
        # src/native/rc_shm.rs). An unqualified clear would then wipe the config that
        # just became current, leaving every evaluation on PROVIDER_NOT_READY.
        self._applied_path: t.Optional[str] = None
        self._configuration_removed = False

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
                self._configuration_removed = True
                log.debug(
                    "Feature Flags: remote_config configuration removed; evaluations return default values until "
                    "configuration is applied again"
                )
                continue

            # Remote Config's own action names: new content for the path already
            # applied is a modify, anything else an apply.
            action = "modify" if payload.path == self._applied_path else "apply"
            try:
                if process_ffe_configuration(payload.content):
                    self._applied_path = payload.path
                    if self._configuration_removed:
                        log.debug("Feature Flags: remote_config configuration recovered after removal")
                        self._configuration_removed = False
                    log.debug(
                        "Feature Flags: remote_config configuration %s applied successfully (%d flag(s), "
                        "config_id=%s, version=%s, configuration_env=%s, created_at=%s)",
                        action,
                        _flag_count(payload.content),
                        payload.metadata.id,
                        payload.metadata.tuf_version,
                        _environment_name(payload.content),
                        _created_at(payload.content),
                    )
                else:
                    log.debug(
                        "Feature Flags: remote_config configuration %s rejected (config_id=%s); %s",
                        action,
                        payload.metadata.id,
                        "the previously applied configuration remains active"
                        if self._applied_path is not None
                        else "no configuration is active",
                    )
            except Exception as e:
                log.debug("Error processing FFE config payload: %s", e, exc_info=True)


def _flag_count(content: t.Any) -> int:
    flags = content.get("flags") if isinstance(content, dict) else None
    return len(flags) if isinstance(flags, dict) else 0


def _environment_name(content: t.Any) -> t.Optional[str]:
    """The environment the delivered configuration was built for, as distinct from DD_ENV."""
    environment = content.get("environment") if isinstance(content, dict) else None
    return environment.get("name") if isinstance(environment, dict) else None


def _created_at(content: t.Any) -> t.Optional[str]:
    return content.get("createdAt") if isinstance(content, dict) else None


# Global callback instance
_featureflag_rc_callback = FeatureFlagCallback()


def enable_featureflags_rc() -> None:
    log.debug("Feature Flags: starting remote_config configuration source (Agent Remote Configuration)")
    remoteconfig_poller.register_callback(
        FFE_FLAGS_PRODUCT,
        _featureflag_rc_callback,
        capabilities=[RemoteConfigCapabilities.FfeFlagConfigurationRules],
    )
    remoteconfig_poller.enable_product(FFE_FLAGS_PRODUCT)


def disable_featureflags_rc() -> None:
    remoteconfig_poller.unregister_callback(FFE_FLAGS_PRODUCT)
    remoteconfig_poller.disable_product(FFE_FLAGS_PRODUCT)
    log.debug("Feature Flags: remote_config configuration delivery stopped")

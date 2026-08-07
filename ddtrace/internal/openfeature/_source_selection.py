"""
Feature Flagging configuration-source selection.

Resolves which delivery source is active (agentless CDN, Agent Remote Config, or
disabled) from the stable kill switch, the explicit source setting, and the
legacy experimental flag used for grandfathering. Mirrors the dd-trace-js
resolution (kill switch -> explicit source -> grandfathering -> default) and the
agentless factory, adapted to dd-trace-py config conventions.
"""

from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._agentless import build_agentless_endpoint
from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource
from ddtrace.internal.settings._core import ValueSource
from ddtrace.internal.settings.openfeature import OpenFeatureConfig


log = get_logger(__name__)

AGENTLESS = "agentless"
REMOTE_CONFIG = "remote_config"
DISABLED = "disabled"

_SOURCE_ENV = "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE"
_LEGACY_ENV = "DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED"


def _provided(ffe_config: OpenFeatureConfig, env_name: str) -> bool:
    """True when the value came from an external source rather than the default."""
    return ffe_config.value_source(env_name) != ValueSource.DEFAULT


def resolve_configuration_source(ffe_config: OpenFeatureConfig) -> str:
    """Resolve the active source: ``agentless``, ``remote_config`` or ``disabled``.

    Precedence (mirrors dd-trace-js):

    1. Stable kill switch off -> disabled.
    2. Explicit source -> use it; an unsupported/reserved value (e.g. ``offline``)
       fails closed to disabled without contacting any source.
    3. Source absent -> grandfather on the legacy experimental flag: explicitly
       true -> remote_config, explicitly false -> disabled.
    4. Otherwise the default -> agentless.
    """
    if not ffe_config.feature_flags_enabled:
        return DISABLED

    source = ffe_config.configuration_source or ""
    if _provided(ffe_config, _SOURCE_ENV) and source:
        if source == AGENTLESS:
            return AGENTLESS
        if source == REMOTE_CONFIG:
            return REMOTE_CONFIG
        log.warning("Unsupported Feature Flagging configuration source %r; provider disabled", source)
        return DISABLED

    # Source absent (unset or blank): preserve legacy Remote Config grandfathering.
    if _provided(ffe_config, _LEGACY_ENV):
        return REMOTE_CONFIG if ffe_config.experimental_flagging_provider_enabled else DISABLED

    return AGENTLESS


def create_agentless_source(
    ffe_config: OpenFeatureConfig, apply_configuration: Callable[["dict[str, Any]"], None]
) -> Optional[AgentlessConfigurationSource]:
    """Build the agentless poller when agentless is the resolved source, else None.

    The default Datadog endpoint requires ``DD_API_KEY`` and sends it. A custom
    endpoint is operator-owned trust: it starts without an API key and omits the
    header, letting the endpoint report any authentication failure itself.
    """
    if resolve_configuration_source(ffe_config) != AGENTLESS:
        return None

    from ddtrace import config as dd_config

    base_url = ffe_config.configuration_source_agentless_base_url
    has_custom_endpoint = bool(base_url and base_url.strip())
    api_key = dd_config._dd_api_key

    if not has_custom_endpoint and not api_key:
        log.error("DD_API_KEY is required for the default Datadog Feature Flagging endpoint")
        return None

    try:
        endpoint = build_agentless_endpoint(dd_config._dd_site, dd_config.env, base_url)
    except ValueError as e:
        log.error("Unable to configure Feature Flagging configuration source: %s", e)
        return None

    return AgentlessConfigurationSource(
        endpoint=endpoint,
        apply_configuration=apply_configuration,
        api_key=None if has_custom_endpoint else api_key,
        poll_interval=ffe_config.configuration_source_agentless_poll_interval_seconds,
        request_timeout=ffe_config.configuration_source_agentless_request_timeout_seconds,
    )

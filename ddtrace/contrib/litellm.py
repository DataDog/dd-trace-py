"""Opt-in, content-free usage attribution for a LiteLLM proxy.

Add ``ddtrace.contrib.litellm.gateway_attribution`` to ``litellm_settings.callbacks``.
See the LiteLLM integration documentation for configuration and coverage limitations.
"""

from ddtrace.contrib.internal.litellm.gateway import GatewayAttribution
from ddtrace.contrib.internal.litellm.gateway import configured_callback
from ddtrace.internal import atexit


gateway_attribution = configured_callback()
atexit.register(gateway_attribution.close)

__all__ = ["GatewayAttribution", "gateway_attribution"]

"""Record LiteLLM gateway usage by authenticated user, without prompt or response text.

Add ``ddtrace.contrib.litellm.gateway_attribution`` to ``litellm_settings.callbacks``.
See the LiteLLM integration guide for setup, optional billing details, and limitations.
"""

from ddtrace.contrib.internal.litellm.gateway import GatewayAttribution
from ddtrace.contrib.internal.litellm.gateway import configured_callback
from ddtrace.internal import atexit


gateway_attribution = configured_callback()
atexit.register(gateway_attribution.close)

__all__ = ["GatewayAttribution", "gateway_attribution"]

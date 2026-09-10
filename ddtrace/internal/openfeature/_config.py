from typing import Any
from typing import Optional

from ddtrace.internal.native._native import ffe


FFE_CONFIG: Optional[ffe.Configuration] = None

# Registry of provider instances (ddtrace.internal.openfeature._provider.DataDogProvider)
# that need to be notified when new FFE configuration arrives. Kept here rather than in
# _provider.py because _native.py sets the configuration and must trigger the
# notification without importing _provider.py (which itself imports _native.py).
# Providers are registered/unregistered via _register_provider/_unregister_provider and
# must implement on_configuration_received().
_provider_instances: list[Any] = []


def _get_ffe_config():
    """Retrieve the current FFE configuration."""
    return FFE_CONFIG


def _set_ffe_config(config):
    """Set the FFE configuration and notify registered providers."""
    global FFE_CONFIG
    FFE_CONFIG = config
    if config is not None:
        _notify_providers_config_received()


def _register_provider(provider: Any) -> None:
    """Register a provider instance for configuration callbacks."""
    if provider not in _provider_instances:
        _provider_instances.append(provider)


def _unregister_provider(provider: Any) -> None:
    """Unregister a provider instance."""
    if provider in _provider_instances:
        _provider_instances.remove(provider)


def _notify_providers_config_received() -> None:
    """Notify all registered providers that configuration was received."""
    for provider in _provider_instances:
        provider.on_configuration_received()

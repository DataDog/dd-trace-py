"""Atomic storage for the parsed FFE configuration and its consent value."""

from typing import Any
from typing import NamedTuple
from typing import Optional
from typing import Union

from ddtrace.internal.native._native import ffe


class _FfeSnapshot(NamedTuple):
    """Native configuration and the consent value from the same UFC."""

    config: ffe.Configuration
    observe_full_evaluation_data: bool


# A single reference keeps configuration and consent consistent when Remote
# Configuration replaces them during an evaluation.
_FFE_SNAPSHOT: Optional[_FfeSnapshot] = None

# Registry of provider instances (ddtrace.internal.openfeature._provider.DataDogProvider)
# that need to be notified when new FFE configuration arrives. Kept here rather than in
# _provider.py because _native.py sets the configuration and must trigger the
# notification without importing _provider.py (which itself imports _native.py).
# Providers are registered/unregistered via _register_provider/_unregister_provider and
# must implement on_configuration_received().
_provider_instances: list[Any] = []


# AIDEV-NOTE: Existing callers may use this compatibility accessor. Evaluation
# code must use _get_ffe_snapshot() so consent stays bound to the configuration.
def _get_ffe_config() -> Optional[ffe.Configuration]:
    """Retrieve only the current native FFE configuration."""
    snapshot = _FFE_SNAPSHOT
    return snapshot.config if snapshot is not None else None


def _get_ffe_snapshot() -> Optional[_FfeSnapshot]:
    """Retrieve the current configuration and consent as one snapshot."""
    return _FFE_SNAPSHOT


def _set_ffe_config(value: Union[None, ffe.Configuration, _FfeSnapshot]) -> None:
    """Set the FFE snapshot and notify registered providers.

    Bare configurations remain supported for existing internal callers and
    fail closed to protected mode.
    """
    global _FFE_SNAPSHOT
    if value is None:
        _FFE_SNAPSHOT = None
    elif isinstance(value, _FfeSnapshot):
        _FFE_SNAPSHOT = value
    else:
        _FFE_SNAPSHOT = _FfeSnapshot(config=value, observe_full_evaluation_data=False)

    if _FFE_SNAPSHOT is not None:
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

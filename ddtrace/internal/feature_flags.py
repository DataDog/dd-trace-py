"""Lets products use Datadog feature flag evaluation without importing the OpenFeature product.

The OpenFeature product registers a module under the ddtrace.feature_flag_providers entry-point
group that implements FeatureFlagProvider. It is imported the first time a product asks for it,
so processes that never evaluate flags don't pay for importing the product.
"""

import typing as t
from typing import Protocol

from ddtrace.internal.entry_points import get_entry_points
from ddtrace.internal.entry_points import is_trusted
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

GROUP = "ddtrace.feature_flag_providers"


class FeatureFlagProvider(Protocol):
    def enable_remote_config(self) -> None: ...

    def set_domain_provider(self, domain: str) -> None: ...


_implementation: t.Optional[FeatureFlagProvider] = None
_plugins: t.Optional[list[t.Any]] = None


def _get_implementation() -> FeatureFlagProvider:
    global _implementation, _plugins

    # Discovery is cached so a missing provider doesn't rescan package metadata on every call; a
    # provider installed later needs a restart. Only a successful load is cached, so a failed
    # import is retried on the next call.
    if _implementation is None:
        if _plugins is None:
            _plugins = [plugin for plugin in get_entry_points(GROUP) if is_trusted(plugin)]
        for plugin in _plugins:
            try:
                _implementation = plugin.load()
                break
            except Exception:
                log.error("Failed to load feature flag provider '%s' (%s)", plugin.name, plugin.value, exc_info=True)

    if _implementation is None:
        raise LookupError("No feature flag provider is installed")
    return _implementation


def enable_remote_config() -> None:
    """Start delivering flag configurations through Agent Remote Configuration."""
    _get_implementation().enable_remote_config()


def set_domain_provider(domain: str) -> None:
    """Register the Datadog OpenFeature provider for domain without waiting for configurations.

    A dedicated domain leaves the application's default provider in place.
    """
    _get_implementation().set_domain_provider(domain)

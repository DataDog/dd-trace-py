"""Centralized environment variable access for dd-trace-py.

This module provides a configuration-aware view of os.environ, enabling
centralized control and validation of all environment variable access in
ddtrace.

All DD_*/_DD_*/OTEL_*/DATADOG_* environment variable accesses are validated
against the registry in supported-configurations.json. Unregistered variables
produce a debug log.

Reads also honor ``CONFIGURATION_ALIASES`` from the registry: a read for a
canonical name falls back to its registered legacy aliases if the canonical
is unset. Aliases registered here must be pure renames (same value space) —
translations like OTEL→DD belong in ``_otel_remapper.py`` instead.
"""

from collections.abc import MutableMapping
import logging
import os
from typing import Iterator
from typing import Optional

from ddtrace.internal.settings._supported_configurations import CONFIGURATION_ALIASES
from ddtrace.internal.settings._supported_configurations import DEPRECATED_CONFIGURATIONS
from ddtrace.internal.settings._supported_configurations import SUPPORTED_CONFIGURATIONS


# Use stdlib logging here instead of ddtrace.internal.logger.get_logger.
# ddtrace/internal/logger.py imports ddtrace.internal.settings.env, so using
# get_logger() would create a circular import at module-load time.
logger = logging.getLogger(__name__)

_ALIAS_TARGETS: frozenset[str] = frozenset(alias for aliases in CONFIGURATION_ALIASES.values() for alias in aliases)
_warned_keys: set[str] = set()


def _get_env_value(key: str) -> Optional[str]:
    """Return the environment value, masking empty values for OTel config variables (OTel specification requirement)"""
    value = os.environ.get(key)

    if key.startswith("OTEL_") and value == "":
        return None
    return value


def _validate_key(key: str) -> None:
    """Warn if a DD_*/_DD_*/OTEL_*/DATADOG_* key is not in the supported-configurations registry.

    Keys that are registered aliases of a supported configuration are also accepted.
    Each unsupported key is warned about at most once per process.
    """
    if not (key.startswith("DD_") or key.startswith("_DD_") or key.startswith("OTEL_") or key.startswith("DATADOG_")):
        return

    if key in _warned_keys:
        return

    if key not in SUPPORTED_CONFIGURATIONS and key not in _ALIAS_TARGETS:
        _warned_keys.add(key)
        logger.debug("Unsupported Datadog configuration variable accessed: %s", key)
    elif key in DEPRECATED_CONFIGURATIONS:
        _warned_keys.add(key)
        logger.debug("Deprecated Datadog configuration variable accessed: %s", key)


class EnvConfig(MutableMapping):
    """A MutableMapping wrapper around os.environ.

    Serves as the centralized entry point for all environment variable access
    in dd-trace-py. Empty OTEL_* values are excluded from reads and iteration.
    Writes and deletes operate directly on os.environ.

    Validates that DD_*/_DD_*/OTEL_*/DATADOG_* accesses use registered
    configuration variables from supported-configurations.json.
    """

    def __getitem__(self, key: str) -> str:
        _validate_key(key)
        if (value := _get_env_value(key)) is not None:
            return value
        for alias in CONFIGURATION_ALIASES.get(key, ()):
            if (value := _get_env_value(alias)) is not None:
                return value
        raise KeyError(key)

    def __setitem__(self, key: str, value: str) -> None:
        _validate_key(key)
        os.environ[key] = value

    def __delitem__(self, key: str) -> None:
        del os.environ[key]

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            _validate_key(key)
            if _get_env_value(key) is not None:
                return True
            return any(_get_env_value(alias) is not None for alias in CONFIGURATION_ALIASES.get(key, ()))
        return key in os.environ

    def __iter__(self) -> Iterator[str]:
        return (key for key in os.environ if _get_env_value(key) is not None)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def copy(self) -> dict:
        return dict(self)


dd_environ = EnvConfig()

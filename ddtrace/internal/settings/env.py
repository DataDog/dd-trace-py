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

The registry also lists the sensitive configurations, whose values must never be reported (e.g. in telemetry).
:func:`get_aliases` and :func:`is_sensitive` expose aliases and sensitivity to other shared code.

This module does not import the registry. The owner of the registry hands it over
with :func:`register_supported_configurations` (dd-trace-py does so at the top of
``ddtrace/__init__.py``). Until that happens, no key is validated (so nothing is
logged), no aliases are applied, i.e. reads look up the exact key in
``os.environ``, and no configuration is known to be sensitive. The first
registration wins; later calls are ignored.
"""

from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
import logging
import os
from typing import Optional


# Use stdlib logging here instead of ddtrace.internal.logger.get_logger.
# ddtrace/internal/logger.py imports ddtrace.internal.settings.env, so using
# get_logger() or logger_name() would create a circular import at module-load time.
# The name is spelled out so it stays under the ddtrace logger once this module moves
# into the ddtrace-internal package.
logger = logging.getLogger("ddtrace.internal.settings.env")

_SUPPORTED_CONFIGURATIONS: Optional[frozenset[str]] = None
_DEPRECATED_CONFIGURATIONS: frozenset[str] = frozenset()
_CONFIGURATION_ALIASES: Mapping[str, Sequence[str]] = {}
_ALIAS_TARGETS: frozenset[str] = frozenset()
_SENSITIVE_CONFIGURATIONS: frozenset[str] = frozenset()
_warned_keys: set[str] = set()


def register_supported_configurations(
    supported: Iterable[str],
    aliases: Mapping[str, Sequence[str]],
    deprecated: Iterable[str],
    sensitive: Iterable[str] = (),
) -> bool:
    """Register the supported-configurations registry. The first registration wins.

    Returns True if this call installed the registry, False if one was already registered.
    """
    global _SUPPORTED_CONFIGURATIONS, _DEPRECATED_CONFIGURATIONS, _CONFIGURATION_ALIASES, _ALIAS_TARGETS
    global _SENSITIVE_CONFIGURATIONS

    if _SUPPORTED_CONFIGURATIONS is not None:
        return False
    _DEPRECATED_CONFIGURATIONS = frozenset(deprecated)
    _SENSITIVE_CONFIGURATIONS = frozenset(sensitive)
    _CONFIGURATION_ALIASES = aliases
    _ALIAS_TARGETS = frozenset(alias for names in aliases.values() for alias in names)
    # Assigned last: it is the "registered" marker checked by _validate_key.
    _SUPPORTED_CONFIGURATIONS = frozenset(supported)
    return True


def get_aliases(name: str) -> Sequence[str]:
    """Return the registered legacy aliases of the canonical configuration ``name``, or none."""
    return _CONFIGURATION_ALIASES.get(name, ())


def is_sensitive(name: str) -> bool:
    """Return whether the configuration ``name`` is registered as sensitive, i.e. its value must not be reported."""
    return name in _SENSITIVE_CONFIGURATIONS


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
    if _SUPPORTED_CONFIGURATIONS is None:
        return

    if not (key.startswith("DD_") or key.startswith("_DD_") or key.startswith("OTEL_") or key.startswith("DATADOG_")):
        return

    if key in _warned_keys:
        return

    if key not in _SUPPORTED_CONFIGURATIONS and key not in _ALIAS_TARGETS:
        _warned_keys.add(key)
        logger.debug("Unsupported Datadog configuration variable accessed: %s", key)
    elif key in _DEPRECATED_CONFIGURATIONS:
        _warned_keys.add(key)
        logger.debug("Deprecated Datadog configuration variable accessed: %s", key)


class EnvConfig(MutableMapping[str, str]):
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
        for alias in _CONFIGURATION_ALIASES.get(key, ()):
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
            return any(_get_env_value(alias) is not None for alias in _CONFIGURATION_ALIASES.get(key, ()))
        return key in os.environ

    def __iter__(self) -> Iterator[str]:
        return (key for key in os.environ if _get_env_value(key) is not None)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def copy(self) -> dict[str, str]:
        return dict(self)


dd_environ = EnvConfig()

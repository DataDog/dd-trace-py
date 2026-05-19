"""Centralized environment variable access for dd-trace-py.

This module provides a drop-in replacement for os.environ, enabling centralized
control and validation of all environment variable access in ddtrace.

All DD_*/_DD_*/OTEL_*/DATADOG_* environment variable accesses are validated
against the registry in supported-configurations.json. Unregistered variables
produce a debug log.

Reads also honor ``CONFIGURATION_ALIASES`` from the registry: a read for a
canonical name falls back to its registered legacy aliases if the canonical
is unset. Aliases registered here must be pure renames (same value space) —
translations like OTEL→DD belong in ``_otel_remapper.py`` instead.

Deprecation handling is driven by ``DEPRECATED_CONFIGURATIONS`` in the
generated registry module. When a deprecated env var is **actually set** by
the user and read via this module, a ``DDTraceDeprecationWarning`` is emitted
once per process. Mere existence probes (``__contains__``) do not fire the
warning. To add a new deprecation, edit ``supported-configurations.json`` —
no code change is required.
"""
from __future__ import annotations

from collections.abc import MutableMapping
import logging
import os
from typing import Iterator

from ddtrace.internal.settings._supported_configurations import CONFIGURATION_ALIASES
from ddtrace.internal.settings._supported_configurations import DEPRECATED_CONFIGURATIONS
from ddtrace.internal.settings._supported_configurations import SUPPORTED_CONFIGURATIONS
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning


# AIDEV-NOTE: Use stdlib logging here instead of ddtrace.internal.logger.get_logger.
# ddtrace/internal/logger.py imports ddtrace.internal.settings.env, so using
# get_logger() would create a circular import at module-load time.
# AIDEV-NOTE: ``debtcollector.deprecate`` is imported lazily inside
# ``_emit_deprecation_warning`` for the same reason: ``ddtrace.vendor`` pulls in
# ``ddtrace.internal.module`` -> ``ddtrace.internal.logger`` -> this module.
logger = logging.getLogger(__name__)

_ALIAS_TARGETS: frozenset[str] = frozenset(alias for aliases in CONFIGURATION_ALIASES.values() for alias in aliases)
_warned_keys: set[str] = set()
_deprecation_warned: set[str] = set()


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


def _emit_deprecation_warning(key: str) -> None:
    """Fire a once-per-process DDTraceDeprecationWarning for a deprecated env var the user actually set."""
    if key in _deprecation_warned:
        return
    info = DEPRECATED_CONFIGURATIONS.get(key)
    if info is None:
        return
    _deprecation_warned.add(key)

    parts = []
    if "replaced_by" in info:
        parts.append(f"Use {info['replaced_by']} instead.")
    if "extra_message" in info:
        parts.append(info["extra_message"])
    message = " ".join(parts) if parts else None

    from ddtrace.vendor.debtcollector import deprecate

    deprecate(
        f"{key} is deprecated",
        message=message,
        removal_version=info.get("removal_version"),
        category=DDTraceDeprecationWarning,
    )


def _maybe_warn_deprecated_read(key: str, source_key: str | None) -> None:
    """Emit a deprecation warning if ``source_key`` (the actual env var found) is deprecated.

    ``key`` is what the caller asked for; ``source_key`` is the env name we resolved the value
    from (could be ``key`` itself, or one of its registered aliases). Either may be deprecated.
    """
    if source_key is not None and source_key in DEPRECATED_CONFIGURATIONS:
        _emit_deprecation_warning(source_key)
    if key != source_key and key in DEPRECATED_CONFIGURATIONS and key in os.environ:
        _emit_deprecation_warning(key)


class EnvConfig(MutableMapping):
    """A MutableMapping wrapper around os.environ.

    Serves as the centralized entry point for all environment variable access
    in dd-trace-py. Drop-in replacement for os.environ — supports reads, writes,
    deletes, containment checks, iteration, and all standard dict-like operations.

    Validates that DD_*/_DD_*/OTEL_*/DATADOG_* accesses use registered
    configuration variables from supported-configurations.json. Emits a
    DDTraceDeprecationWarning on reads of deprecated env vars whose values
    are set in os.environ.
    """

    def __getitem__(self, key: str) -> str:
        _validate_key(key)
        if (value := os.environ.get(key)) is not None:
            _maybe_warn_deprecated_read(key, key)
            return value
        for alias in CONFIGURATION_ALIASES.get(key, ()):
            if (value := os.environ.get(alias)) is not None:
                _maybe_warn_deprecated_read(key, alias)
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
            if key in os.environ:
                return True
            return any(alias in os.environ for alias in CONFIGURATION_ALIASES.get(key, ()))
        return key in os.environ

    def __iter__(self) -> Iterator[str]:
        return iter(os.environ)

    def __len__(self) -> int:
        return len(os.environ)

    def copy(self) -> dict:
        return dict(self)


dd_environ = EnvConfig()

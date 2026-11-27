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
:func:`get_aliases`, :func:`is_sensitive` and :func:`is_reportable` expose aliases and sensitivity to other shared
code.

This module does not import the registry at import time. The registry is resolved lazily, the first time a
lookup needs it (validation, aliases or sensitivity), in this order of precedence:

1. An explicit :func:`register_supported_configurations` call made before that first lookup. The first
   registration wins; later calls, and the entry point, are ignored.
2. The module named by the ``ddtrace_internal.supported_configurations`` entry point, which must provide
   ``SUPPORTED_CONFIGURATIONS``, ``CONFIGURATION_ALIASES``, ``DEPRECATED_CONFIGURATIONS`` and
   ``SENSITIVE_CONFIGURATIONS``. It is looked up once and cached. If it is missing or fails to load, one
   message is logged and the registry stays unregistered.

While unregistered, no key is validated (so nothing is logged), no aliases are applied, i.e. reads look up the
exact key in ``os.environ``, and no configuration is known to be sensitive. Since that does not make them safe to
report, :func:`is_reportable` fails closed: nothing is reportable without a registry.
"""

from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
import logging
import os
from typing import Any
from typing import Optional


# Use stdlib logging here instead of ddtrace.internal.logger.get_logger.
# ddtrace/internal/logger.py imports ddtrace.internal.settings.env, so using
# get_logger() would create a circular import at module-load time.
logger = logging.getLogger(__name__)

_SUPPORTED_CONFIGURATIONS: Optional[frozenset[str]] = None
_DEPRECATED_CONFIGURATIONS: frozenset[str] = frozenset()
_CONFIGURATION_ALIASES: Mapping[str, Sequence[str]] = {}
_ALIAS_TARGETS: frozenset[str] = frozenset()
_SENSITIVE_CONFIGURATIONS: frozenset[str] = frozenset()
_warned_keys: set[str] = set()

ENTRY_POINT_GROUP = "ddtrace_internal.supported_configurations"
# Set once the registry has been resolved (from an explicit registration or the entry point), even if that
# found nothing, so that the entry point is looked up at most once.
_resolved = False
_resolving = False


def _load_entry_point_module(group: str) -> Any:
    """Return the module declared by the first entry point of ``group`` that loads, or None if there is none."""
    # Imported here: the helper lives under ddtrace, which this module must not import when it is loaded.
    from ddtrace.internal.utils.entry_points import entry_points_with_distributions

    for entry_point, _ in entry_points_with_distributions(group):
        return entry_point.load()
    return None


def _install(
    supported: Iterable[str],
    aliases: Mapping[str, Sequence[str]],
    deprecated: Iterable[str],
    sensitive: Iterable[str],
) -> None:
    global _SUPPORTED_CONFIGURATIONS, _DEPRECATED_CONFIGURATIONS, _CONFIGURATION_ALIASES, _ALIAS_TARGETS
    global _SENSITIVE_CONFIGURATIONS

    _DEPRECATED_CONFIGURATIONS = frozenset(deprecated)
    _SENSITIVE_CONFIGURATIONS = frozenset(sensitive)
    _CONFIGURATION_ALIASES = aliases
    _ALIAS_TARGETS = frozenset(alias for names in aliases.values() for alias in names)
    # Assigned last: it is the "registered" marker checked by _validate_key.
    _SUPPORTED_CONFIGURATIONS = frozenset(supported)


def _resolve() -> None:
    """Resolve the registry from the entry point, unless one is already registered. Never raises.

    There is no lock: two threads racing here load the same module and install the same values, and the registry
    marker is assigned last, so a reader never sees a half-installed registry as registered.
    """
    global _resolved, _resolving

    # `_resolving` guards against re-entry (e.g. the load triggers an env lookup).
    if _resolved or _resolving:
        return
    _resolving = True
    try:
        if _SUPPORTED_CONFIGURATIONS is None:
            try:
                module = _load_entry_point_module(ENTRY_POINT_GROUP)
                if module is not None:
                    _install(
                        module.SUPPORTED_CONFIGURATIONS,
                        module.CONFIGURATION_ALIASES,
                        module.DEPRECATED_CONFIGURATIONS,
                        module.SENSITIVE_CONFIGURATIONS,
                    )
            except Exception:
                logger.warning(
                    "Could not load the supported configurations registry; configurations are not validated",
                    exc_info=True,
                )
    finally:
        _resolving = False
        _resolved = True


def register_supported_configurations(
    supported: Iterable[str],
    aliases: Mapping[str, Sequence[str]],
    deprecated: Iterable[str],
    sensitive: Iterable[str] = (),
) -> bool:
    """Register the supported-configurations registry. The first registration wins.

    An explicit registration takes precedence over the ``ddtrace_internal.supported_configurations`` entry point,
    provided it happens before the first lookup that needs the registry, which resolves the entry point.

    Returns True if this call installed the registry, False if one was already registered or resolved from the
    entry point. A registration still succeeds after a lookup that found no entry point.
    """
    global _resolved

    if _SUPPORTED_CONFIGURATIONS is not None:
        return False
    _install(supported, aliases, deprecated, sensitive)
    _resolved = True
    return True


def get_aliases(name: str) -> Sequence[str]:
    """Return the registered legacy aliases of the canonical configuration ``name``, or none."""
    if not _resolved:
        _resolve()
    return _CONFIGURATION_ALIASES.get(name, ())


def is_sensitive(name: str) -> bool:
    """Return whether the configuration ``name`` is registered as sensitive, i.e. its value must not be reported."""
    if not _resolved:
        _resolve()
    return name in _SENSITIVE_CONFIGURATIONS


def is_reportable(name: str) -> bool:
    """Return whether the value of the configuration ``name`` may be reported, e.g. in telemetry.

    Unlike ``not is_sensitive(name)``, this fails closed: without a registry nothing is known to be safe, so
    nothing is reportable.
    """
    if not _resolved:
        _resolve()
    return _SUPPORTED_CONFIGURATIONS is not None and name not in _SENSITIVE_CONFIGURATIONS


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
    if not _resolved:
        _resolve()
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

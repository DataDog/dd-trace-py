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

This module does not import the registry at import time. The registry is resolved lazily, the first time a
lookup needs it (validation, aliases or sensitivity), in this order of precedence:

1. An explicit :func:`register_supported_configurations` call made before that first lookup. The first
   registration wins; later calls, and the entry point, are ignored.
2. The module named by the ``ddtrace_internal.supported_configurations`` entry point, which must provide
   ``SUPPORTED_CONFIGURATIONS``, ``CONFIGURATION_ALIASES``, ``DEPRECATED_CONFIGURATIONS`` and
   ``SENSITIVE_CONFIGURATIONS``. It is looked up once and cached. Providers are only loaded when they are
   declared by a trusted ddtrace distribution and resolve into its module namespaces (the same policy as
   :func:`ddtrace.internal.entry_points.is_trusted`, which this module cannot import without a cycle); other
   providers are skipped. If no trusted provider is found, or it fails to load, one message is logged and the
   registry stays unregistered.

While unregistered, no key is validated (so nothing is logged), no aliases are applied, i.e. reads look up the
exact key in ``os.environ``, and no configuration is known to be sensitive.
"""

from _thread import allocate_lock as _allocate_lock
from _thread import get_ident as _get_ident
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
# Only ddtrace's own distributions may provide the registry: loading an entry point executes the provider
# module, and this resolution can happen during an ordinary `import ddtrace` (logger initialization reads
# configuration). Mirrors ddtrace.internal.entry_points.is_trusted, which cannot be imported here because
# it imports the logger, which imports this module.
_TRUSTED_DISTRIBUTIONS = frozenset({"ddtrace", "ddtrace-serverless", "ddtrace-internal"})
_TRUSTED_MODULE_PREFIXES = frozenset({"ddtrace.", "_ddtrace_internal."})
# Set once the registry has been resolved (from an explicit registration or the entry point), even if that
# found nothing, so that the entry point is looked up at most once.
_resolved = False
# Serializes resolution: a concurrent first lookup waits until the registry is fully installed instead of
# proceeding without aliases. The raw _thread lock (the unpatched C primitive ddtrace.internal.locks also
# uses) is taken deliberately: this module must not import ddtrace, and gevent's monkey-patched
# threading.Lock must not be shared with ddtrace's real threads.
_resolution_lock = _allocate_lock()
# The thread currently inside _resolve, to tell re-entry (the registry module performing an env lookup while
# it loads) from a concurrent caller: the former must proceed unregistered, the latter must wait.
_resolving_thread: Optional[int] = None


def _is_resolved() -> bool:
    """Whether the registry was resolved (registered, or the entry point was consulted and found nothing)."""
    # Read through a function so type checkers do not narrow the module global at the under-lock re-checks
    # below: another thread may have resolved it while we waited for the lock.
    return _resolved


def _is_registered() -> bool:
    """Whether a registry is installed."""
    # Same as above: the global is re-read under the lock after another thread may have installed one.
    return _SUPPORTED_CONFIGURATIONS is not None


def _load_entry_point_module(group: str) -> Any:
    """Return the module declared by the first trusted entry point of ``group``, or None if there is none.

    Providers from any other distribution, or resolving outside ddtrace's module namespaces, are skipped:
    loading one would execute third-party code during ordinary ddtrace imports.
    """
    # Imported here: the helper lives under ddtrace, which this module must not import when it is loaded.
    from ddtrace.internal.utils.entry_points import distribution_name
    from ddtrace.internal.utils.entry_points import entry_points_with_distributions
    from ddtrace.internal.utils.entry_points import is_trusted_entry_point

    for entry_point, dist in entry_points_with_distributions(group):
        if not is_trusted_entry_point(entry_point, dist, _TRUSTED_DISTRIBUTIONS, _TRUSTED_MODULE_PREFIXES):
            logger.warning(
                "Refusing to load %s entry point '%s' from untrusted distribution '%s' (module: %s)",
                group,
                entry_point.name,
                distribution_name(dist),
                entry_point.value,
            )
            continue
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

    Concurrent first lookups wait on the lock until the registry is fully installed, so every caller sees
    complete registry data (aliases, sensitivity). Re-entry from the loader itself — the registry module
    performing an env lookup while it imports — returns immediately, unregistered, instead of deadlocking;
    the outer call installs the registry before it returns.
    """
    global _resolved, _resolving_thread

    if _resolved:
        return
    if _resolving_thread == _get_ident():
        # Re-entry from the load itself (e.g. the load triggers an env lookup).
        return
    with _resolution_lock:
        if _is_resolved():
            # Another thread resolved the registry while we waited for the lock.
            return
        _resolving_thread = _get_ident()
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
            _resolving_thread = None
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
    if _resolving_thread == _get_ident():
        # Called from inside the entry-point load: the registry it is installing wins.
        return False
    with _resolution_lock:
        if _is_registered():
            # Another thread installed a registry while we waited for the lock.
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

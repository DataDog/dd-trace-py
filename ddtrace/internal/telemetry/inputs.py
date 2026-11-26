"""What telemetry needs from the libraries it reports for, pulled from entry points rather than imported.

Telemetry moves to the ddtrace-internal package, which never imports ddtrace, so it can't read dd-trace-py's
version, code locations or product settings itself. It pulls them instead, the first time it needs them, from
distributions that declare entry points. Two kinds of input reach it.

**The library**, declared in the ``ddtrace_internal.library`` group::

    [project.entry-points.'ddtrace_internal.library']
    ddtrace = "ddtrace._error_attribution"

- The name and version are the declaring distribution's, read from its metadata without importing anything.
- The code roots and the integration resolver are the ``code_roots`` and ``integration_of`` attributes of the
  entry point's object, loaded only when an error needs attributing.

If several distributions declare one, any distribution other than ``ddtrace-testing`` wins, so a standalone
ddtrace-testing reports ``ddtrace`` when dd-trace-py is installed. ``register_library()`` overrides the entry
point, for tests and for callers that know their identity better than their metadata does.

**Providers**, declared in the ``ddtrace_internal.telemetry_providers`` group, e.g.
``appsec = "ddtrace.internal.settings.appsec_telemetry:provider"``. A provider is an object with optional hooks:
``configurations()``, ``dependency_metadata_enabled()``, ``endpoint_collection_enabled()`` and
``endpoint_collection_limit()``. The providers are loaded in entry point name order, and the hooks are called on each
use, so runtime changes to the settings behind them apply. A missing provider or hook means the documented default
applies.

Importing this module resolves nothing, and everything resolved is cached.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import NamedTuple
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.entry_points import distribution_name
from ddtrace.internal.utils.entry_points import entry_points_with_distributions


if TYPE_CHECKING:
    from importlib.metadata import Distribution
    from importlib.metadata import EntryPoint


log = get_logger(__name__)

GROUP = "ddtrace_internal.library"
PROVIDERS_GROUP = "ddtrace_internal.telemetry_providers"
# Reports for ddtrace-testing only when no other library declares an identity.
_FALLBACK_DISTRIBUTION = "ddtrace-testing"


def _no_integration(filename: str) -> Optional[str]:
    return None


def _semver(version: str) -> str:
    """Format a PEP 440 version as the SemVer the Agent expects, e.g. ``1.7.1rc2`` as ``1.7.1-rc2``.

    The same formatting as ``ddtrace.internal.utils.version``, which telemetry can't import.
    """
    if "rc" in version and "-rc" not in version:
        return version.replace("rc", "-rc", 1)
    if ".dev" in version:
        return version.replace(".dev", "-dev", 1)
    return version


class Library(NamedTuple):
    """The library telemetry reports for."""

    # Distribution name. Telemetry never reports the library as one of its own dependencies.
    name: str
    # Reported as the application's tracer_version.
    version: str
    # Sent with each product change.
    product_version: str


class _Attribution(NamedTuple):
    # Path fragments that mark a file as the library's code, e.g. `ddtrace/`. A file whose name contains one of
    # them is library code.
    code_roots: tuple[str, ...]
    # Maps the name of a file to the integration it belongs to, or None.
    integration_of: Callable[[str], Optional[str]]


# Without an entry point (no installed metadata, e.g. running from a source tree) nothing is attributed, and the
# version is the one ddtrace.version falls back to without metadata.
_UNKNOWN_LIBRARY = Library("", "0.0.0", "0.0.0")
_NO_ATTRIBUTION = _Attribution((), _no_integration)

_registered: Optional[tuple[Library, _Attribution]] = None
_entry_point_looked_up = False
_entry_point: Optional[tuple["EntryPoint", "Distribution"]] = None
_library: Optional[Library] = None
_attribution: Optional[_Attribution] = None


def register_library(
    name: str,
    version: str,
    product_version: str,
    code_roots: tuple[str, ...] = (),
    integration_of: Callable[[str], Optional[str]] = _no_integration,
) -> None:
    """Set the library telemetry reports for, overriding the entry point.

    The first registration wins; later ones are ignored. Call it before telemetry starts: what was already
    reported keeps the identity it was reported with.
    """
    global _registered

    if _registered is None:
        _registered = (Library(name, version, product_version), _Attribution(tuple(code_roots), integration_of))


def _find_entry_point() -> Optional[tuple["EntryPoint", "Distribution"]]:
    global _entry_point, _entry_point_looked_up

    if not _entry_point_looked_up:
        found = entry_points_with_distributions(GROUP)
        preferred = [ep for ep in found if distribution_name(ep[1]) != _FALLBACK_DISTRIBUTION]
        candidates = preferred or found
        _entry_point = candidates[0] if candidates else None
        _entry_point_looked_up = True
        if _entry_point is None:
            log.debug("No distribution declares a %s entry point: telemetry reports an unknown library", GROUP)
    return _entry_point


def get_library() -> Library:
    """Return the library telemetry reports for: the registered one, else the entry point's."""
    global _library

    if _registered is not None:
        return _registered[0]
    if _library is None:
        found = _find_entry_point()
        if found is None:
            _library = _UNKNOWN_LIBRARY
        else:
            dist = found[1]
            _library = Library(dist.metadata["Name"], dist.version, _semver(dist.version))
    return _library


def _get_attribution() -> _Attribution:
    global _attribution

    if _registered is not None:
        return _registered[1]
    if _attribution is None:
        found = _find_entry_point()
        attribution = _NO_ATTRIBUTION
        if found is not None:
            try:
                provider = found[0].load()
                attribution = _Attribution(
                    tuple(getattr(provider, "code_roots", ())),
                    getattr(provider, "integration_of", _no_integration),
                )
            except Exception:
                log.debug("Failed to load the %s entry point %s", GROUP, found[0].value, exc_info=True)
        _attribution = attribution
    return _attribution


def is_library_code(filename: str) -> bool:
    """Return whether ``filename`` is in the code the library declares as its own."""
    return any(root in filename for root in _get_attribution().code_roots)


def get_integration(filename: str) -> Optional[str]:
    """Return the name of the integration the library says ``filename`` belongs to, or None."""
    return _get_attribution().integration_of(filename)


_providers: Optional[tuple[Any, ...]] = None

# What telemetry does when no provider says otherwise.
DEFAULT_ENDPOINT_COLLECTION_LIMIT = 300


def get_providers() -> tuple[Any, ...]:
    """Return the loaded telemetry providers, in entry point name order. Resolved on first call, then cached."""
    global _providers

    # No lock: two threads resolving at once load the same entry points, and the last assignment wins, equal to the
    # first.
    if _providers is None:
        providers = []
        for entry_point, _ in sorted(entry_points_with_distributions(PROVIDERS_GROUP), key=lambda ep: ep[0].name):
            try:
                providers.append(entry_point.load())
            except Exception:
                log.debug("Failed to load the %s entry point %s", PROVIDERS_GROUP, entry_point.value, exc_info=True)
        _providers = tuple(providers)
    return _providers


def _get_hooks(name: str) -> list[Callable[[], Any]]:
    """Return the hook ``name`` of every provider that defines it."""
    return [hook for hook in (getattr(p, name, None) for p in get_providers()) if callable(hook)]


def get_configurations() -> tuple[Any, ...]:
    """Return the configuration objects the providers ask telemetry to report, in provider order. None by default."""
    return tuple(config for hook in _get_hooks("configurations") for config in hook())


def dependency_metadata_enabled() -> bool:
    """Return whether full dependency metadata is collected, as the first provider that says so decides.

    False if no provider says.
    """
    for hook in _get_hooks("dependency_metadata_enabled"):
        return bool(hook())
    return False


def endpoint_collection_enabled() -> bool:
    """Return whether API endpoints are collected, as the first provider that says so decides. True if none does."""
    for hook in _get_hooks("endpoint_collection_enabled"):
        return bool(hook())
    return True


def endpoint_collection_limit() -> int:
    """Return the most endpoints to send in a message. ``DEFAULT_ENDPOINT_COLLECTION_LIMIT`` if no provider says."""
    for hook in _get_hooks("endpoint_collection_limit"):
        return int(hook())
    return DEFAULT_ENDPOINT_COLLECTION_LIMIT

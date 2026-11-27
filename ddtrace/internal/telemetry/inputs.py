"""The library telemetry reports for: its identity, the code it owns and how its integrations are named.

Telemetry moves to the ddtrace-internal package, which never imports ddtrace, so it can't read dd-trace-py's
version or code locations itself. It pulls them instead, the first time it needs them, from the library that
declares an entry point in the ddtrace_internal.library group::

    [project.entry-points.'ddtrace_internal.library']
    ddtrace = "ddtrace._error_attribution"

- The name and version are the declaring distribution's, read from its metadata without importing anything.
- The code roots and the integration resolver are the code_roots and integration_of attributes of the
  entry point's object. The object is loaded when the excepthook is installed (see load_attribution).

Only ddtrace's own distributions (ddtrace, ddtrace-serverless, ddtrace-internal and ddtrace-testing) and module
namespaces may provide the library: other entry points are ignored with a warning, as the module is executed while
ddtrace imports.

A distribution that only wants to report when no other library is installed declares its entry point in the
ddtrace_internal.library.fallback group instead. The first entry point of the primary group wins; the fallback
group is consulted only when that one is empty. register_library() overrides the entry points, for tests and
for callers that know their identity better than their metadata does.

Importing this module resolves nothing, and everything resolved is cached.
"""

from typing import TYPE_CHECKING
from typing import Callable
from typing import NamedTuple
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.entry_points import distribution_name
from ddtrace.internal.utils.entry_points import entry_points_with_distributions
from ddtrace.internal.utils.entry_points import is_trusted_entry_point


if TYPE_CHECKING:
    from importlib.metadata import Distribution
    from importlib.metadata import EntryPoint


log = get_logger(__name__)

GROUP = "ddtrace_internal.library"
# Loading the library's entry point executes its module while ddtrace is importing, so, like the settings
# registry, only ddtrace's own distributions and module namespaces may provide it: an unrelated installed
# distribution must not run code merely because ddtrace starts.
_TRUSTED_DISTRIBUTIONS = frozenset({"ddtrace", "ddtrace-serverless", "ddtrace-internal", "ddtrace-testing"})
_TRUSTED_MODULE_PREFIXES = frozenset({"ddtrace.", "_ddtrace_internal."})
# Entry points of this group are used only when no distribution declares one in GROUP.
FALLBACK_GROUP = "ddtrace_internal.library.fallback"


def _no_integration(filename: str) -> Optional[str]:
    return None


def _semver(version: str) -> str:
    """Format a PEP 440 version as the SemVer the Agent expects, e.g. 1.7.1rc2 as 1.7.1-rc2.

    The same formatting as ddtrace.internal.utils.version, which telemetry can't import.
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


def _trusted_entry_points(group: str) -> list[tuple["EntryPoint", "Distribution"]]:
    """Return the entry points of group that a trusted distribution declares, in path order."""
    trusted = []
    for entry_point, dist in entry_points_with_distributions(group):
        if is_trusted_entry_point(entry_point, dist, _TRUSTED_DISTRIBUTIONS, _TRUSTED_MODULE_PREFIXES):
            trusted.append((entry_point, dist))
        else:
            log.warning(
                "Refusing to use the %s entry point %r of the untrusted distribution %r",
                group,
                entry_point.value,
                distribution_name(dist),
            )
    return trusted


def _find_entry_point() -> Optional[tuple["EntryPoint", "Distribution"]]:
    global _entry_point, _entry_point_looked_up

    if not _entry_point_looked_up:
        try:
            found = _trusted_entry_points(GROUP) or _trusted_entry_points(FALLBACK_GROUP)
        except Exception:
            # Malformed metadata of any installed distribution makes the scan raise; that must not break startup.
            log.debug("Failed to look up the %s entry points", GROUP, exc_info=True)
            found = []
        _entry_point = found[0] if found else None
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


def load_attribution() -> None:
    """Load what the library declares about its code now, instead of when an error first needs attributing.

    Called as the excepthook is installed, while the library is still importing. If the library's own import
    fails, Python drops it from sys.modules, and loading its entry point afterwards would import it again, fail
    the same way and attribute nothing. Loaded early, the module stays in sys.modules and is found there.
    """
    _get_attribution()


def is_library_code(filename: str) -> bool:
    """Return whether filename is in the code the library declares as its own."""
    return any(root in filename for root in _get_attribution().code_roots)


def get_integration(filename: str) -> Optional[str]:
    """Return the name of the integration the library says filename belongs to, or None."""
    return _get_attribution().integration_of(filename)

import collections
from functools import lru_cache as cached
from functools import singledispatch
from importlib.machinery import PathFinder
from importlib.machinery import all_suffixes
import inspect
import logging
import os
from pathlib import Path
import sys
import sysconfig
import threading
from types import ModuleType
import typing as t

from ddtrace.internal import forksafe
from ddtrace.internal.module import origin
from ddtrace.internal.native import scan_distributions
from ddtrace.internal.native.exceptions import is_panic_exception
from ddtrace.internal.settings.third_party import config as tp_config
from ddtrace.internal.threads import Thread
from ddtrace.internal.utils.cache import callonce
from ddtrace.internal.utils.paths import deepest_containing_root
from ddtrace.internal.utils.paths import is_contained
from ddtrace.internal.utils.paths import relative_parts


LOG = logging.getLogger(__name__)


class Distribution(t.NamedTuple):
    name: str
    version: str


# dist.metadata access is per-dist defensive — malformed METADATA
# (rare but real on system-Python / CI images) must not poison the @callonce cache.
_BAD_DISTS_WARNED: set[str] = set()


def _bad_dist_key(dist) -> str:
    """A stable identifier for deduping warnings about a malformed dist.

    ``Distribution._path`` is a private attribute but the most useful key in
    practice for path-based installs (the dist-info directory). For other
    backends we fall back to ``repr(dist)``.
    """
    if isinstance(dist, str):
        # A path from the native scan.
        return dist
    path = getattr(dist, "_path", None)
    if path is not None:
        return str(path)
    return repr(dist)


def _warn_bad_dist(dist, exc: t.Union[BaseException, str]) -> None:
    """Log a one-time warning per malformed dist; subsequent calls are silent.

    The first warning includes ``exc_info=True`` so an operator can identify
    the broken package; further encounters are suppressed to keep CI logs
    bounded.
    """
    key = _bad_dist_key(dist)
    if key in _BAD_DISTS_WARNED:
        return
    _BAD_DISTS_WARNED.add(key)
    LOG.debug(
        "Skipping distribution with unreadable metadata at %s: %s",
        key,
        exc,
        exc_info=exc if isinstance(exc, BaseException) else False,
    )


def get_distributions() -> t.Mapping[str, str]:
    """returns the mapping from distribution name to version for all distributions in a python path"""
    return _installed().versions


def get_package_distributions() -> t.Mapping[str, list[str]]:
    """a mapping of importable package names to their distribution name(s)"""
    return _installed().packages


@cached(maxsize=1024)
def get_module_distribution_versions(module_name: str) -> t.Optional[tuple[str, str]]:
    if not module_name:
        return None

    names: list[str] = []
    # The fast path of _installed, inlined: this runs once per imported module.
    snapshot = _INSTALLED
    if snapshot is None or not snapshot.checked or snapshot.sys_path != sys.path or snapshot.meta_path != sys.meta_path:
        snapshot = _installed()
    pkgs = snapshot.packages
    dist_map = snapshot.versions
    while names == []:
        # First try to resolve the module name from package distributions
        version = dist_map.get(module_name)
        if version:
            return (module_name, version)
        # Since we've failed to resolve, try to resolve the parent package
        names = pkgs.get(module_name, [])
        if not names:
            p = module_name.rfind(".")
            if p > 0:
                module_name = module_name[:p]
            else:
                break
    if len(names) != 1:
        # either it was not resolved due to multiple packages with the same name
        # or it's a multipurpose package (like '__pycache__')
        return None
    return (names[0], get_version_for_package(names[0]))


@cached(maxsize=1024)
def get_version_for_package(name: str) -> str:
    """returns the version of a package"""
    import importlib.metadata as importlib_metadata

    try:
        return importlib_metadata.version(name)
    except Exception:
        return ""


@cached(maxsize=256)
def _is_regular_package(parent: Path, base: str) -> bool:
    """Whether parent/base is a regular package (it ships an __init__.py).

    Keyed on the top-level name, not the caller's full relative path, which would
    spend a cache entry per source file and almost never hit.
    """
    root = parent / base
    return root.is_dir() and (root / "__init__.py").exists()


def _effective_root(rel_parts: tuple[str, ...], parent: Path) -> str:
    base = rel_parts[0]
    return base if _is_regular_package(parent, base) else "/".join(rel_parts[:2])


# DEV: Since we can't lock on sys.path, these operations can be racy.
_SYS_PATH_HASH: t.Optional[int] = None
_RESOLVED_SYS_PATH: t.List[Path] = []  # noqa: UP006


def resolve_sys_path() -> list[Path]:
    global _SYS_PATH_HASH, _RESOLVED_SYS_PATH

    if (h := hash(tuple(sys.path))) != _SYS_PATH_HASH:
        _SYS_PATH_HASH = h
        _RESOLVED_SYS_PATH = [Path(_).resolve() for _ in sys.path]

    return _RESOLVED_SYS_PATH


def _root_module(path: Path, resolved: Path) -> str:
    # Most likely prefixes first, and against the resolved path to get the
    # shortest relative one. The sys.path probe below keeps the path as given.
    for parent_path in (purelib_path, platlib_path):
        parts = relative_parts(resolved, parent_path)
        if parts:
            return _effective_root(parts, parent_path)

    # Try to resolve the root module using sys.path. We keep the shortest
    # relative path as the one more likely to give us the root module.
    hit = deepest_containing_root(path, resolve_sys_path())
    if hit is not None:
        parent_path, parts = hit
        if parts:
            return _effective_root(parts, parent_path)

    # Bazel runfiles support: we assume that these paths look like
    # /some/path.runfiles/<distribution_name>/site-packages/<root_module>/...
    # /usr/local/runfiles/<distribution_name>/site-packages/<root_module>/...
    for s in path.parents:
        if s.parent.name == "site-packages":
            return s.name

    msg = f"Could not find root module for path {path}"
    raise ValueError(msg)


def _normalized_dist_name(name: str) -> str:
    """Normalize a distribution name for comparison (PEP 503-ish).

    ``.dist-info`` / ``.egg-info`` directories escape the project name (dashes
    become underscores), so fold ``-``, ``_`` and ``.`` to a single form and
    lowercase before comparing (``google-cloud-storage`` == ``google_cloud_storage``).
    """
    return name.replace("-", "_").replace(".", "_").lower()


@cached(maxsize=256)
def _shipped_distributions(directory: Path) -> frozenset[str]:
    """Normalized names of the distributions whose metadata directory ships.

    One cached listing serves both callers below. Keying on the directory alone
    is what allows that, and an install root is routinely large enough that
    re-walking it per probe is worth avoiding.
    """
    try:
        return frozenset(
            # {name}-{version}.dist-info / {name}.egg-info: the name part
            # (escaped, so it never contains a dash) precedes the first dash.
            _normalized_dist_name(child.name[: -len(child.suffix)].split("-", 1)[0])
            for child in directory.iterdir()
            if child.suffix in (".dist-info", ".egg-info")
        )
    except OSError:
        # Not a directory, missing, or unreadable: ships nothing.
        return frozenset()


def _is_install_root(directory: Path) -> bool:
    """Whether ``directory`` ships any distribution metadata.

    A sys.path entry named ``site-packages`` is the usual install target, but
    distributions can also be installed onto an arbitrary directory (``pip
    install --target=...``, vendored dependencies dropped on ``PYTHONPATH``).
    The presence of a ``*.dist-info`` / ``*.egg-info`` child marks such a
    directory as a candidate anchor. This is necessary but not sufficient: a
    source checkout carries its own project ``*.egg-info`` yet does not own
    unrelated dependency namespaces living in the same tree, so the match is
    additionally gated on distribution ownership in _install_root_owner.
    """
    return bool(_shipped_distributions(directory))


def _root_ships_distribution(directory: Path, dist_name: str) -> bool:
    """Whether ``directory`` contains the metadata of ``dist_name`` itself.

    Distinguishes a genuine install root of ``dist_name`` (its own
    ``*.dist-info`` / ``*.egg-info`` is present) from an unrelated directory
    that merely happens to carry some other distribution's metadata.
    """
    return _normalized_dist_name(dist_name) in _shipped_distributions(directory)


def _install_root_owner(path: Path, mapping: dict[str, Distribution]) -> t.Optional[Distribution]:
    """Longest-prefix lookup for dependencies installed outside site-packages.

    Handles ``pip install --target`` and vendored deps dropped on sys.path.
    Unlike a site-packages root, such a directory is only trusted when it
    verifiably ships the matched distribution's own metadata; otherwise an
    editable source checkout (which carries its own project ``.egg-info``)
    would capture unrelated namespace files sharing its tree and misreport
    user code as that dependency.
    """
    for parent_path in resolve_sys_path():
        if parent_path.name == "site-packages" or not _is_install_root(parent_path):
            continue
        parts = relative_parts(path, parent_path)
        if parts is None:
            continue
        for end in range(len(parts), 0, -1):
            hit = mapping.get("/".join(parts[:end]))
            if hit is not None:
                if _root_ships_distribution(parent_path, hit.name):
                    return hit
                break
    return None


def _relative_to_known_root(path: Path, resolved: Path) -> t.Optional[tuple[str, ...]]:
    """Return path's components relative to the site-packages-like root containing it.
    Only trusted dependency roots are considered (purelib/platlib and
    site-packages dirs). Install roots outside site-packages are handled by
    _install_root_owner, which additionally verifies distribution ownership.
    Returns None when path is not under such a root.
    """
    for parent_path in (purelib_path, platlib_path):
        parts = relative_parts(resolved, parent_path)
        if parts is not None:
            return parts

    hit = deepest_containing_root(path, (p for p in resolve_sys_path() if p.name == "site-packages"))
    if hit is not None:
        return hit[1]

    for s in path.parents:
        if s.parent.name == "site-packages":
            parts = relative_parts(path, s.parent)
            if parts is not None:
                return parts
    return None


# (name, version or None, import root keys, top-level names)
_DistributionRecord = tuple[str, t.Optional[str], list[str], list[str]]


_WarnBadDist = t.Callable[[t.Any, t.Union[BaseException, str]], None]


def _python_dist_records(
    dists: t.Iterable[t.Any], warn: _WarnBadDist = _warn_bad_dist
) -> t.Iterator[_DistributionRecord]:
    """Records via importlib, for custom meta path finders; also the reference for the native scan."""
    # Cache per directory prefix whether it is a *regular* package (a directory
    # that ships an ``__init__.py``). PEP 420 namespace packages have no
    # ``__init__.py`` at their shared levels, so several distributions can
    # contribute siblings under the same prefix (``google/cloud/storage`` vs
    # ``google/cloud/bigquery``). The key must therefore be the deepest
    # importable root, not a fixed 2-level prefix, otherwise every sibling
    # collapses onto whichever dist was scanned first and the longest-prefix
    # lookup in filename_to_package has nothing specific to match.
    regular_pkg: dict[Path, bool] = {}

    def root_key(f) -> str:
        parts = f.parts
        n = len(parts)
        if n < 2:
            # Top-level module file (e.g. ``six.py``); keep the file name.
            return parts[0]

        # zipfile.Path has no .parents. ancestors[k] is k levels up.
        ancestors: list[t.Any] = []
        for depth in range(1, n):
            if not ancestors:
                ancestors.append(f.locate())
                for _ in range(n - 1):
                    ancestors.append(ancestors[-1].parent)
            pkg_dir = ancestors[n - depth]
            is_regular = regular_pkg.get(pkg_dir)
            if is_regular is None:
                is_regular = pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists()
                regular_pkg[pkg_dir] = is_regular
            if is_regular:
                # First regular package on the path: this is the import root.
                return "/".join(parts[:depth])

        # Every directory level is a namespace (no __init__.py anywhere on the
        # path). Two distributions can then contribute module files directly
        # under the shared namespace (dist A ships ``acme/foo.py``, dist B ships
        # ``acme/bar.py``); dropping the file name collapses both onto the bare
        # ``acme`` key and attributes every sibling to whichever dist was
        # scanned first. Keep the full path (including the file name) so each
        # module gets a distinct key the longest-prefix lookup can match.
        return "/".join(parts)

    # per-dist try/except — one bad dist used to collapse the whole
    # mapping to None (silently breaking is_third_party for the rest of the process).
    for dist in dists:
        try:
            metadata = dist.metadata
            name = metadata["name"]
            version = metadata["version"] or None
        except Exception as exc:
            warn(dist, exc)
            continue
        if not name:
            continue

        # A broken file list must not cost the name, version or declared top-level names.
        try:
            declared = _top_level_declared(dist)
        except Exception as exc:
            warn(dist, exc)
            declared = []
        files: list[t.Any] = []
        keys: list[str] = []
        top_level: list[str] = declared
        try:
            files = list(dist.files or [])
            if version is not None:
                for f in files:
                    root = f.parts[0]
                    if root.endswith(".dist-info") or root.endswith(".egg-info") or root == "..":
                        continue
                    keys.append(root_key(f))
            if not declared:
                top_level = [n for n in {_get_toplevel_name(f) for f in files} if "." not in n]
        except Exception as exc:
            warn(dist, exc)
        yield name, version, keys, top_level


# Each sys.path entry with its mtime, and the meta path layout: custom finders in
# order, with None for the native sys.path scan.
_CacheKey = tuple[tuple[tuple[str, t.Optional[int]], ...], tuple[t.Any, ...]]


class _Installed:
    """Installed distributions and the maps derived from them."""

    # Slots and eager maps keep attribute access on the read path specialised.
    __slots__ = ("key", "records", "sys_path", "meta_path", "checked", "versions", "packages", "mapping")

    def __init__(self, key: _CacheKey, records: list[_DistributionRecord]) -> None:
        self.key = key
        self.records = records
        # For the per-read check: list equality compares items by identity first.
        self.sys_path = list(sys.path)
        self.meta_path = list(sys.meta_path)
        # Whether a read has done the full check (mtimes, custom finders).
        self.checked = False

        versions: dict[str, str] = {}
        packages = collections.defaultdict(list)
        mapping: dict[str, Distribution] = {}
        for name, version, keys, top_level in records:
            for pkg in top_level:
                packages[pkg].append(name)
            if version is None:
                continue
            versions[name.lower()] = version
            d = Distribution(name=name, version=version)
            for root in keys:
                if root not in mapping:
                    mapping[root] = d
        self.versions = versions
        self.packages = dict(packages)
        self.mapping = mapping


_INSTALLED: t.Optional[_Installed] = None
# (mtime, records) per sys.path entry, so only new or changed entries are rescanned.
_ENTRY_RECORDS: dict[str, tuple[t.Optional[int], list[_DistributionRecord]]] = {}
# Fork-safe, as lazy scans run on threads that may fork; reentrant, so a stray
# re-entry repeats work rather than deadlocks.
_INSTALLED_DISTRIBUTIONS_LOCK = forksafe.RLock()
# Set while this thread asks custom finders for their distributions.
_FINDER_QUERY = threading.local()

# The boot-time scan: the thread is set while it is pending, the event when it ends.
_PREFETCH_THREAD: t.Optional[Thread] = None
_PREFETCH_DONE = forksafe.Event()
# Set in the prefetch thread, which must not wait for itself.
_IN_PREFETCH = threading.local()


def _reset_installed_distributions() -> None:
    """Forget all scanned records."""
    global _INSTALLED, _PREFETCH_THREAD
    if (thread := _PREFETCH_THREAD) is not None:
        thread.join()
    with _INSTALLED_DISTRIBUTIONS_LOCK:
        _INSTALLED = None
        _ENTRY_RECORDS.clear()
    _PREFETCH_THREAD = None
    _PREFETCH_DONE.clear()


def _resolve_entry(entry: str) -> str:
    # Key relative entries by where they point now: the working directory can change.
    if os.path.isabs(entry):
        return entry
    try:
        return os.path.abspath(entry)
    except OSError:
        # The working directory is gone: nothing to list, as for importlib.
        return entry


def _mtime(entry: str) -> t.Optional[int]:
    # Like importlib, notice installs into an entry by its mtime.
    try:
        return os.stat(entry).st_mtime_ns
    except OSError:
        return None


def _scans_sys_path(finder: t.Any) -> bool:
    # PathFinder, or the importlib_metadata backport's MetadataPathFinder, which
    # finds the same distributions.
    name = finder.__name__ if isinstance(finder, type) else type(finder).__name__
    return finder is PathFinder or name == "MetadataPathFinder"


def _meta_path_layout() -> tuple[t.Any, ...]:
    layout: list[t.Any] = []
    for finder in sys.meta_path:
        if _scans_sys_path(finder):
            # One native scan stands for all of them.
            if None not in layout:
                layout.append(None)
        elif getattr(finder, "find_distributions", None) is not None:
            layout.append(finder)
    return tuple(layout)


def _cache_key() -> _CacheKey:
    # A repeated entry would list its distributions twice.
    entries = dict.fromkeys(_resolve_entry(e) for e in sys.path if isinstance(e, str))
    return tuple((entry, _mtime(entry)) for entry in entries), _meta_path_layout()


def _installed(check: bool = True) -> _Installed:
    """The current snapshot of the installed distributions, in importlib discovery order.

    Every read checks sys.path and the meta path, which is cheap. The first read
    after a build also checks entry mtimes and asks custom finders again.
    """
    global _INSTALLED
    snapshot = _INSTALLED
    if (
        snapshot is not None
        and snapshot.checked
        and snapshot.sys_path == sys.path
        and snapshot.meta_path == sys.meta_path
    ):
        return snapshot

    if _PREFETCH_THREAD is not None and not getattr(_IN_PREFETCH, "active", False):
        # The boot-time scan is pending: wait for it rather than race it.
        _PREFETCH_DONE.wait()
        snapshot = _INSTALLED

    key = _cache_key()
    layout = key[1]
    custom = any(finder is not None for finder in layout)
    if snapshot is not None and snapshot.key == key and not custom:
        # Same records: only the full check was pending, or nothing relevant changed.
        snapshot.checked = snapshot.checked or check
        snapshot.sys_path = list(sys.path)
        snapshot.meta_path = list(sys.meta_path)
        return snapshot

    problems: list[tuple[t.Any, t.Union[BaseException, str]]] = []

    def warn(dist: t.Any, exc: t.Union[BaseException, str]) -> None:
        problems.append((dist, exc))

    if getattr(_FINDER_QUERY, "active", False):
        # A custom finder is reading the maps: don't recurse into it. Its
        # distributions are missing, so neither publish nor keep lookups from this.
        _FINDER_QUERY.nested = True
        segments: list[t.Optional[list[_DistributionRecord]]] = [None] if None in layout else []
        with _INSTALLED_DISTRIBUTIONS_LOCK:
            snapshot = _Installed(key, list(_distribution_records(key, segments, warn)))
    else:
        # Custom finders run arbitrary Python, which could re-enter: ask them
        # outside the lock.
        _FINDER_QUERY.active = True
        _FINDER_QUERY.nested = False
        try:
            segments = _meta_path_segments(layout, warn)
        finally:
            _FINDER_QUERY.active = False
        if _FINDER_QUERY.nested:
            _clear_lookup_caches()
        replaced = False
        with _INSTALLED_DISTRIBUTIONS_LOCK:
            previous = _INSTALLED
            if custom or previous is None or previous.key != key:
                snapshot = _Installed(key, list(_distribution_records(key, segments, warn)))
                _INSTALLED = snapshot
                replaced = previous is not None
            else:
                snapshot = previous
            snapshot.checked = snapshot.checked or check
        if replaced:
            # Lookups cached from the previous snapshot may no longer hold.
            _clear_lookup_caches()
    # Log outside the lock: handlers may do I/O and yield under gevent.
    for dist, exc in problems:
        _warn_bad_dist(dist, exc)
    return snapshot


def _clear_lookup_caches() -> None:
    # Lookups cached on the maps go stale when their records are replaced or incomplete.
    # The directory probes behind filename_to_package go with them: a replaced snapshot
    # usually means something was installed, which can change what a directory ships.
    for lookup in (
        get_module_distribution_versions,
        filename_to_package,
        module_to_package,
        is_third_party,
        _is_user_code_str,
        _shipped_distributions,
        _is_regular_package,
    ):
        lookup.cache_clear()


def _installed_distributions() -> list[_DistributionRecord]:
    """Records for every installed distribution, in importlib discovery order."""
    return _installed().records


def _prefetch() -> None:
    _IN_PREFETCH.active = True
    try:
        _installed(check=False)
    except Exception:
        # The lazy path retries on first use.
        LOG.debug("Failed to prefetch installed distributions", exc_info=True)
    except BaseException as exc:
        if not is_panic_exception(exc):
            raise
        LOG.debug("Failed to prefetch installed distributions", exc_info=True)
    finally:
        _end_prefetch()


def _end_prefetch() -> None:
    global _PREFETCH_THREAD
    _PREFETCH_THREAD = None
    _PREFETCH_DONE.set()


def prefetch_distributions() -> None:
    """Scan the installed distributions in the background, on boot.

    Readers that arrive before the scan ends wait for it; forks join it.
    """
    global _PREFETCH_THREAD
    if _INSTALLED is not None or _PREFETCH_THREAD is not None:
        return
    thread = Thread(_prefetch, name=f"{__name__}:prefetch")
    _PREFETCH_THREAD = thread
    try:
        thread.start()
    except Exception:
        # No thread to wait for: readers scan on first use instead.
        LOG.debug("Failed to start the installed distributions prefetch", exc_info=True)
        _end_prefetch()


@forksafe.register
def _reset_prefetch_after_fork() -> None:
    # A start queued during a fork never runs in the child: nothing may wait for it.
    _end_prefetch()


def _entry_records(entry: str, module_suffixes: list[str], warn: _WarnBadDist) -> t.Optional[list[_DistributionRecord]]:
    """Records for the distributions under one sys.path entry; None if the scan failed."""
    try:
        # Only the prefetch thread, which is joined before interpreter shutdown,
        # may release the GIL: see scan_distributions.
        dists, errors = scan_distributions(entry, module_suffixes, getattr(_IN_PREFETCH, "active", False))
    except Exception as exc:
        warn(entry, exc)
        return None
    for path, error in errors:
        warn(path, error)
    return dists


def _meta_path_segments(layout: tuple[t.Any, ...], warn: _WarnBadDist) -> list[t.Optional[list[_DistributionRecord]]]:
    """Distribution sources in meta path order: None for the sys.path scan (native), records for custom finders."""
    segments: list[t.Optional[list[_DistributionRecord]]] = []
    for finder in layout:
        if finder is None:
            segments.append(None)
            continue
        # Imported lazily: IAST drops importlib.metadata after boot, for gevent.
        import importlib.metadata as importlib_metadata

        try:
            dists = getattr(finder, "find_distributions")(importlib_metadata.DistributionFinder.Context())
            segments.append(list(_python_dist_records(dists, warn)))
        except Exception as exc:
            warn(finder, exc)
    return segments


def _distribution_records(
    key: _CacheKey,
    segments: list[t.Optional[list[_DistributionRecord]]],
    warn: _WarnBadDist = _warn_bad_dist,
) -> t.Iterator[_DistributionRecord]:
    """Records in discovery order; call with the scan lock held."""
    # Longest first, as inspect.getmodulename tries them.
    module_suffixes = sorted(all_suffixes(), key=len, reverse=True)

    for segment in segments:
        if segment is not None:
            yield from segment
            continue
        for entry, mtime in key[0]:
            cached = _ENTRY_RECORDS.get(entry)
            if cached is not None and cached[0] == mtime:
                yield from cached[1]
                continue
            records = _entry_records(entry, module_suffixes, warn)
            if records is None:
                # Not cached, so the next rebuild tries again.
                continue
            _ENTRY_RECORDS[entry] = (mtime, records)
            yield from records


_MAPPING_FAILURE_LOGGED = False


def _package_for_root_module_mapping() -> t.Optional[dict[str, Distribution]]:
    global _MAPPING_FAILURE_LOGGED
    try:
        return _installed().mapping
    except Exception:
        if not _MAPPING_FAILURE_LOGGED:
            _MAPPING_FAILURE_LOGGED = True
            LOG.warning(
                "Unable to enumerate installed distributions, "
                "please report this to https://github.com/DataDog/dd-trace-py/issues",
                exc_info=True,
            )
        return None


@callonce
def _third_party_packages() -> set:
    from gzip import decompress
    from importlib.resources import read_binary

    return (
        set(decompress(read_binary("ddtrace.internal", "third-party.tar.gz")).decode("utf-8").splitlines())
        | tp_config.includes
    ) - tp_config.excludes


@cached(maxsize=16384)
def filename_to_package(filename: t.Union[str, Path]) -> t.Optional[Distribution]:
    mapping = _package_for_root_module_mapping()
    if mapping is None:
        return None

    try:
        path = Path(filename) if isinstance(filename, str) else filename

        # Resolved once and threaded through both probes below, which used to
        # resolve it each: this dominates the cost of a cache miss. Passed
        # alongside the original rather than replacing it, because the sys.path
        # probes must match on the path as given or symlinked entries are missed.
        resolved = path.resolve()

        # Longest-prefix match against the mapping. Namespace distributions can
        # share an intermediate level (google/cloud/storage vs
        # google/cloud/bigquery), so the most specific (deepest) mapped prefix
        # must win; _root_module only yields a fixed 2-level key and cannot tell
        # the siblings apart. The probe is anchored at the site-packages-relative
        # root, so a subpackage that happens to share a name with another
        # top-level dist cannot mismatch.
        parts = _relative_to_known_root(path, resolved)
        if parts is not None:
            for end in range(len(parts), 0, -1):
                hit = mapping.get("/".join(parts[:end]))
                if hit is not None:
                    return hit

        # Dependencies installed outside site-packages (pip install --target,
        # vendored deps on sys.path): anchor only when the root verifiably owns
        # the matched distribution, so an editable source checkout carrying its
        # own .egg-info cannot capture unrelated namespace files as a dependency.
        owner = _install_root_owner(path, mapping)
        if owner is not None:
            return owner

        root_module_path = _root_module(path, resolved)
        if root_module_path in mapping:
            return mapping[root_module_path]

        # Loop through mapping and check the distribution name, since the key isn't always the same, for example:
        #   '__editable__.ddtrace-3.9.0.dev...pth': Distribution(name='ddtrace', version='...')
        for distribution in mapping.values():
            if distribution.name == root_module_path:
                return distribution

        return None
    except (ValueError, OSError):
        return None


@cached(maxsize=256)
def module_to_package(module: ModuleType) -> t.Optional[Distribution]:
    """Returns the package distribution for a module"""
    module_origin = origin(module)
    return filename_to_package(module_origin) if module_origin is not None else None


stdlib_path = Path(sysconfig.get_path("stdlib")).resolve()
platstdlib_path = Path(sysconfig.get_path("platstdlib")).resolve()
purelib_path = Path(sysconfig.get_path("purelib")).resolve()
platlib_path = Path(sysconfig.get_path("platlib")).resolve()


@cached(maxsize=256)
def is_stdlib(path: Path) -> bool:
    rpath = path
    if not rpath.is_absolute() or rpath.is_symlink():
        rpath = rpath.resolve()

    return (is_contained(rpath, stdlib_path) or is_contained(rpath, platstdlib_path)) and not (
        is_contained(rpath, purelib_path) or is_contained(rpath, platlib_path)
    )


@cached(maxsize=256)
def is_third_party(path: Path) -> bool:
    package = filename_to_package(path)
    if package is None:
        return False

    return package.name in _third_party_packages()


@singledispatch
def is_user_code(path) -> bool:
    raise NotImplementedError(f"Unsupported type {type(path)}")


@is_user_code.register
def _(path: Path) -> bool:
    return not (is_stdlib(path) or is_third_party(path))


# DEV: Creating Path objects on Python < 3.11 is expensive
@is_user_code.register(str)
@cached(maxsize=1024)
def _is_user_code_str(path: str) -> bool:
    _path = Path(path)
    return not (is_stdlib(_path) or is_third_party(_path))


@cached(maxsize=256)
def is_distribution_available(name: str) -> bool:
    """Determine if a distribution is available in the current environment."""
    import importlib.metadata as importlib_metadata

    try:
        importlib_metadata.distribution(name)
    except importlib_metadata.PackageNotFoundError:
        return False

    return True


# ----
# the below helpers are copied from importlib_metadata
# ----


def _top_level_declared(dist):
    return (dist.read_text("top_level.txt") or "").split()


def _topmost(name) -> t.Optional[str]:
    """
    Return the top-most parent as long as there is a parent.
    """
    top, *rest = name.parts
    return top if rest else None


def _get_toplevel_name(name) -> str:
    """
    Infer a possibly importable module name from a name presumed on
    sys.path.
    >>> _get_toplevel_name(PackagePath('foo.py'))
    'foo'
    >>> _get_toplevel_name(PackagePath('foo'))
    'foo'
    >>> _get_toplevel_name(PackagePath('foo.pyc'))
    'foo'
    >>> _get_toplevel_name(PackagePath('foo/__init__.py'))
    'foo'
    >>> _get_toplevel_name(PackagePath('foo.pth'))
    'foo.pth'
    >>> _get_toplevel_name(PackagePath('foo.dist-info'))
    'foo.dist-info'
    """
    return _topmost(name) or (
        # python/typeshed#10328
        inspect.getmodulename(name) or str(name)
    )

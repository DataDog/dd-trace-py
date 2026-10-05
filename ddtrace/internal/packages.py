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
from types import ModuleType
import typing as t

from ddtrace.internal import forksafe
from ddtrace.internal.module import origin
from ddtrace.internal.native import scan_distributions
from ddtrace.internal.native.exceptions import is_panic_exception
from ddtrace.internal.settings.third_party import config as tp_config
from ddtrace.internal.utils.cache import callonce


LOG = logging.getLogger(__name__)


class Distribution(t.NamedTuple):
    name: str
    version: str


_PACKAGE_DISTRIBUTIONS: t.Optional[t.Mapping[str, t.List[str]]] = None  # noqa: UP006

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


@callonce
def get_distributions() -> t.Mapping[str, str]:
    """returns the mapping from distribution name to version for all distributions in a python path"""
    return {name.lower(): version for name, version, _, _ in _installed_distributions() if version is not None}


def get_package_distributions() -> t.Mapping[str, list[str]]:
    """a mapping of importable package names to their distribution name(s)"""
    global _PACKAGE_DISTRIBUTIONS
    if _PACKAGE_DISTRIBUTIONS is None:
        pkg_to_dist = collections.defaultdict(list)
        for name, _, _, top_level in _installed_distributions():
            for pkg in top_level:
                pkg_to_dist[pkg].append(name)
        _PACKAGE_DISTRIBUTIONS = dict(pkg_to_dist)
    return _PACKAGE_DISTRIBUTIONS


@cached(maxsize=1024)
def get_module_distribution_versions(module_name: str) -> t.Optional[tuple[str, str]]:
    if not module_name:
        return None

    names: list[str] = []
    pkgs = get_package_distributions()
    dist_map = get_distributions()
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


def _effective_root(rel_path: Path, parent: Path) -> str:
    base = rel_path.parts[0]
    root = parent / base
    return base if root.is_dir() and (root / "__init__.py").exists() else "/".join(rel_path.parts[:2])


# DEV: Since we can't lock on sys.path, these operations can be racy.
_SYS_PATH_HASH: t.Optional[int] = None
_RESOLVED_SYS_PATH: t.List[Path] = []  # noqa: UP006


def resolve_sys_path() -> list[Path]:
    global _SYS_PATH_HASH, _RESOLVED_SYS_PATH

    if (h := hash(tuple(sys.path))) != _SYS_PATH_HASH:
        _SYS_PATH_HASH = h
        _RESOLVED_SYS_PATH = [Path(_).resolve() for _ in sys.path]

    return _RESOLVED_SYS_PATH


def _root_module(path: Path) -> str:
    # Try the most likely prefixes first
    for parent_path in (purelib_path, platlib_path):
        try:
            # Resolve the path to use the shortest relative path.
            return _effective_root(path.resolve().relative_to(parent_path), parent_path)
        except ValueError:
            # Not relative to this path
            pass

    # Try to resolve the root module using sys.path. We keep the shortest
    # relative path as the one more likely to give us the root module.
    min_relative_path = max_parent_path = None
    for parent_path in resolve_sys_path():
        try:
            relative = path.relative_to(parent_path)
            if min_relative_path is None or len(relative.parents) < len(min_relative_path.parents):
                min_relative_path, max_parent_path = relative, parent_path
        except ValueError:
            pass

    if min_relative_path is not None:
        try:
            return _effective_root(min_relative_path, t.cast(Path, max_parent_path))
        except IndexError:
            pass

    # Bazel runfiles support: we assume that these paths look like
    # /some/path.runfiles/<distribution_name>/site-packages/<root_module>/...
    # /usr/local/runfiles/<distribution_name>/site-packages/<root_module>/...
    for s in path.parents:
        if s.parent.name == "site-packages":
            return s.name

    msg = f"Could not find root module for path {path}"
    raise ValueError(msg)


@cached(maxsize=256)
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
    try:
        if not directory.is_dir():
            return False
        for child in directory.iterdir():
            if child.suffix in (".dist-info", ".egg-info"):
                return True
    except OSError:
        return False
    return False


def _normalized_dist_name(name: str) -> str:
    """Normalize a distribution name for comparison (PEP 503-ish).

    ``.dist-info`` / ``.egg-info`` directories escape the project name (dashes
    become underscores), so fold ``-``, ``_`` and ``.`` to a single form and
    lowercase before comparing (``google-cloud-storage`` == ``google_cloud_storage``).
    """
    return name.replace("-", "_").replace(".", "_").lower()


def _root_ships_distribution(directory: Path, dist_name: str) -> bool:
    """Whether ``directory`` contains the metadata of ``dist_name`` itself.

    Distinguishes a genuine install root of ``dist_name`` (its own
    ``*.dist-info`` / ``*.egg-info`` is present) from an unrelated directory
    that merely happens to carry some other distribution's metadata.
    """
    target = _normalized_dist_name(dist_name)
    try:
        children = list(directory.iterdir())
    except OSError:
        return False
    for child in children:
        if child.suffix not in (".dist-info", ".egg-info"):
            continue
        # ``{name}-{version}.dist-info`` / ``{name}.egg-info``: the name part
        # (escaped, so it never contains a dash) precedes the first dash.
        candidate = child.name[: -len(child.suffix)].split("-", 1)[0]
        if _normalized_dist_name(candidate) == target:
            return True
    return False


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
        try:
            relative = path.relative_to(parent_path)
        except ValueError:
            continue
        parts = relative.parts
        for end in range(len(parts), 0, -1):
            hit = mapping.get("/".join(parts[:end]))
            if hit is not None:
                if _root_ships_distribution(parent_path, hit.name):
                    return hit
                break
    return None


def _relative_to_known_root(path: Path) -> t.Optional[Path]:
    """Return path relative to the site-packages-like root that contains it.
    Only trusted dependency roots are considered (purelib/platlib and
    site-packages dirs). Install roots outside site-packages are handled by
    _install_root_owner, which additionally verifies distribution ownership.
    Returns None when path is not under such a root.
    """
    for parent_path in (purelib_path, platlib_path):
        try:
            return path.resolve().relative_to(parent_path)
        except ValueError:
            pass

    min_relative_path: t.Optional[Path] = None
    for parent_path in resolve_sys_path():
        if parent_path.name != "site-packages":
            continue
        try:
            relative = path.relative_to(parent_path)
        except ValueError:
            continue
        if min_relative_path is None or len(relative.parents) < len(min_relative_path.parents):
            min_relative_path = relative
    if min_relative_path is not None:
        return min_relative_path

    for s in path.parents:
        if s.parent.name == "site-packages":
            try:
                return path.relative_to(s.parent)
            except ValueError:
                pass
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


# (sys.path entries, records), as one tuple so lock-free readers never mix them up.
_INSTALLED_DISTRIBUTIONS: t.Optional[tuple[tuple[str, ...], list[_DistributionRecord]]] = None
# Records per sys.path entry, so a sys.path change only scans the new entries.
_ENTRY_RECORDS: dict[str, list[_DistributionRecord]] = {}
# Fork-safe because lazy scans run on application threads, which may fork
# mid-scan. Reentrant so an unexpected re-entry repeats work instead of deadlocking.
_INSTALLED_DISTRIBUTIONS_LOCK = forksafe.RLock()

# The scan is I/O-bound; more threads than this barely help.
_PREFETCH_MAX_THREADS = 4


def _reset_installed_distributions() -> None:
    """Forget all scanned records."""
    global _INSTALLED_DISTRIBUTIONS
    with _INSTALLED_DISTRIBUTIONS_LOCK:
        _INSTALLED_DISTRIBUTIONS = None
        _ENTRY_RECORDS.clear()


def _sys_path_entries() -> tuple[str, ...]:
    # A repeated entry would list its distributions twice.
    return tuple(dict.fromkeys(e for e in sys.path if isinstance(e, str)))


def _installed_distributions(threads: int = 1) -> list[_DistributionRecord]:
    """Records for every installed distribution, in importlib discovery order.

    The sys.path scan is native, as importlib is far too slow at it. Records
    follow the current sys.path, so entries added after the boot-time prefetch
    are still seen. Only prefetch_distributions may ask for threads.
    """
    global _INSTALLED_DISTRIBUTIONS
    entries = _sys_path_entries()
    if (installed := _INSTALLED_DISTRIBUTIONS) is not None and installed[0] == entries:
        return installed[1]

    problems: list[tuple[t.Any, t.Union[BaseException, str]]] = []

    def warn(dist: t.Any, exc: t.Union[BaseException, str]) -> None:
        problems.append((dist, exc))

    # Custom finders run arbitrary Python, whose import hooks could re-enter
    # mid-scan; keep them out of the lock.
    segments = _meta_path_segments(warn)
    with _INSTALLED_DISTRIBUTIONS_LOCK:
        if (installed := _INSTALLED_DISTRIBUTIONS) is None or installed[0] != entries:
            records = list(_distribution_records(entries, segments, threads, warn))
            installed = _INSTALLED_DISTRIBUTIONS = (entries, records)
    # Log outside the lock: handlers may do I/O and yield under gevent.
    for dist, exc in problems:
        _warn_bad_dist(dist, exc)
    return installed[1]


def prefetch_distributions() -> None:
    """Scan the installed distributions on boot.

    Uses threads unless bootstrapped by ddtrace.auto, which can run with the
    application's threads alive: no ddtrace thread may be alive across a fork.
    """
    threads = 1
    if "ddtrace.auto" not in sys.modules:
        # Only some platforms have sched_getaffinity, which honours CPU affinity.
        sched_getaffinity = getattr(os, "sched_getaffinity", None)
        cpus = len(sched_getaffinity(0)) if sched_getaffinity is not None else (os.cpu_count() or 1)
        threads = min(_PREFETCH_MAX_THREADS, cpus)
    try:
        _installed_distributions(threads=threads)
    except Exception:
        # The lazy path retries on first use.
        LOG.debug("Failed to prefetch installed distributions", exc_info=True)
    except BaseException as exc:
        # A PanicException at startup would abort the process.
        if not is_panic_exception(exc):
            raise
        LOG.debug("Failed to prefetch installed distributions", exc_info=True)


def _entry_records(
    entry: str, module_suffixes: list[str], threads: int, warn: _WarnBadDist
) -> list[_DistributionRecord]:
    """Records for the distributions under one sys.path entry."""
    try:
        dists, errors = scan_distributions(entry, module_suffixes, threads)
    except Exception as exc:
        # One bad entry must not cost all the others.
        warn(entry, exc)
        return []
    for path, error in errors:
        warn(path, error)
    return dists


def _meta_path_segments(warn: _WarnBadDist) -> list[t.Optional[list[_DistributionRecord]]]:
    """Distribution sources on sys.meta_path, in order: None for PathFinder (scanned natively), records for others."""
    import importlib.metadata as importlib_metadata

    segments: list[t.Optional[list[_DistributionRecord]]] = []
    for finder in sys.meta_path:
        if finder is PathFinder:
            segments.append(None)
        elif type(finder).__name__ == "MetadataPathFinder":
            # The importlib_metadata backport's duplicate of PathFinder.
            continue
        elif (find_distributions := getattr(finder, "find_distributions", None)) is not None:
            try:
                dists = find_distributions(importlib_metadata.DistributionFinder.Context())
                segments.append(list(_python_dist_records(dists, warn)))
            except Exception as exc:
                warn(finder, exc)
    return segments


def _distribution_records(
    entries: tuple[str, ...],
    segments: list[t.Optional[list[_DistributionRecord]]],
    threads: int = 1,
    warn: _WarnBadDist = _warn_bad_dist,
) -> t.Iterator[_DistributionRecord]:
    """Records in discovery order; call with the scan lock held."""
    # Longest first, as inspect.getmodulename tries them.
    module_suffixes = sorted(all_suffixes(), key=len, reverse=True)

    for segment in segments:
        if segment is not None:
            yield from segment
            continue
        for entry in entries:
            if (records := _ENTRY_RECORDS.get(entry)) is None:
                records = _ENTRY_RECORDS[entry] = _entry_records(entry, module_suffixes, threads, warn)
            yield from records


@callonce
def _package_for_root_module_mapping() -> t.Optional[dict[str, Distribution]]:
    try:
        records = _installed_distributions()
    except Exception:
        LOG.warning(
            "Unable to enumerate installed distributions, "
            "please report this to https://github.com/DataDog/dd-trace-py/issues",
            exc_info=True,
        )
        return None

    mapping: dict[str, Distribution] = {}
    for name, version, keys, _ in records:
        if version is None:
            continue
        d = Distribution(name=name, version=version)
        for key in keys:
            if key not in mapping:
                mapping[key] = d
    return mapping


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

        # Longest-prefix match against the mapping. Namespace distributions can
        # share an intermediate level (google/cloud/storage vs
        # google/cloud/bigquery), so the most specific (deepest) mapped prefix
        # must win; _root_module only yields a fixed 2-level key and cannot tell
        # the siblings apart. The probe is anchored at the site-packages-relative
        # root, so a subpackage that happens to share a name with another
        # top-level dist cannot mismatch.
        relative = _relative_to_known_root(path)
        if relative is not None:
            parts = relative.parts
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

        # Avoid calling .resolve() on the path here to prevent breaking symlink matching in `_root_module`.
        root_module_path = _root_module(path)
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

    return (rpath.is_relative_to(stdlib_path) or rpath.is_relative_to(platstdlib_path)) and not (
        rpath.is_relative_to(purelib_path) or rpath.is_relative_to(platlib_path)
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
def _(path: str) -> bool:
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

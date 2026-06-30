import collections
from functools import lru_cache as cached
from functools import singledispatch
import inspect
import logging
from pathlib import Path
import sys
import sysconfig
from types import ModuleType
import typing as t

from ddtrace.internal.module import origin
from ddtrace.internal.settings import env
from ddtrace.internal.settings.third_party import config as tp_config
from ddtrace.internal.utils.cache import callonce


LOG = logging.getLogger(__name__)

Distribution = t.NamedTuple("Distribution", [("name", str), ("version", str)])


_PACKAGE_DISTRIBUTIONS: t.Optional[t.Mapping[str, t.List[str]]] = None  # noqa: UP006

# AIDEV-NOTE: dist.metadata access is per-dist defensive -- malformed METADATA
# (rare but real on system-Python / CI images) must not poison the @callonce cache.
_BAD_DISTS_WARNED: set[str] = set()


def _bad_dist_key(dist) -> str:
    """A stable identifier for deduping warnings about a malformed dist.

    ``Distribution._path`` is a private attribute but the most useful key in
    practice for path-based installs (the dist-info directory). For other
    backends we fall back to ``repr(dist)``.
    """
    path = getattr(dist, "_path", None)
    if path is not None:
        return str(path)
    return repr(dist)


def _warn_bad_dist(dist, exc: BaseException) -> None:
    """Log a one-time warning per malformed dist; subsequent calls are silent.

    The first warning includes ``exc_info=True`` so an operator can identify
    the broken package; further encounters are suppressed to keep CI logs
    bounded.
    """
    key = _bad_dist_key(dist)
    if key in _BAD_DISTS_WARNED:
        return
    _BAD_DISTS_WARNED.add(key)
    LOG.debug("Skipping distribution with unreadable metadata at %s: %s", key, exc, exc_info=True)


@callonce
def get_distributions() -> t.Mapping[str, str]:
    """returns the mapping from distribution name to version for all distributions in a python path"""
    import importlib.metadata as importlib_metadata

    pkgs = {}
    for dist in importlib_metadata.distributions():
        try:
            # PKG-INFO and/or METADATA files are parsed when dist.metadata is accessed.
            # Optimization: we should avoid accessing dist.metadata more than once.
            metadata = dist.metadata
            name = metadata["name"]
            version = metadata["version"]
            if name and version:
                pkgs[name.lower()] = version
        except Exception as exc:
            _warn_bad_dist(dist, exc)

    return pkgs


def get_package_distributions() -> t.Mapping[str, list[str]]:
    """a mapping of importable package names to their distribution name(s)"""
    global _PACKAGE_DISTRIBUTIONS
    if _PACKAGE_DISTRIBUTIONS is None:
        import importlib.metadata as importlib_metadata

        # Prefer the official API if available, otherwise fallback to the vendored version
        if hasattr(importlib_metadata, "packages_distributions"):
            _PACKAGE_DISTRIBUTIONS = importlib_metadata.packages_distributions()
        else:
            _PACKAGE_DISTRIBUTIONS = _packages_distributions()
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


def _relative_to_known_root(path: Path) -> t.Optional[Path]:
    """Return ``path`` relative to the site-packages-like root that contains it.

    Shares root resolution with ``_root_module`` (purelib/platlib, then
    ``sys.path``, then the Bazel ``*/site-packages`` heuristic) but yields the
    *full* relative path instead of a fixed-depth key. ``filename_to_package``
    uses it to do longest-prefix matching against the mapping, which is what
    lets namespace distributions sharing an intermediate level resolve
    correctly: ``google-cloud-storage`` (``google/cloud/storage``) and
    ``google-cloud-bigquery`` (``google/cloud/bigquery``) both reduce to the
    ``google/cloud`` key under ``_root_module``, so it cannot tell them apart.

    Only *dependency* roots are considered: ``purelib``/``platlib`` and
    ``site-packages`` dirs (the latter is where Bazel isolates each no-RECORD
    dependency). Generic ``sys.path`` source/workspace roots are deliberately
    excluded -- a Bazel binary puts its own source roots on ``sys.path`` too,
    and applying dependency namespace keys to them would misclassify user code
    (e.g. a workspace ``google/cloud/...`` file while ``google-cloud-*`` is in
    runfiles) as third-party. Those paths fall back to ``_root_module``.

    Returns ``None`` when ``path`` is not under such a root.
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


def _in_bazel() -> bool:
    """Whether the process is running under Bazel runfiles (dir or manifest mode)."""
    return bool(env.get("RUNFILES_DIR") or env.get("RUNFILES_MANIFEST_FILE"))


def _manifest_real_path(line: str) -> t.Optional[str]:
    r"""The real (target) path from one Bazel runfiles MANIFEST line.

    Two formats exist (see the Bazel runfiles manifest spec):
      - plain ``<runfiles_path> <real_path>`` (split on the first space), and
      - an escaped form used when either path contains a space, newline, or
        backslash: the line starts with a space and both fields escape ``\\s``
        (space), ``\\n`` (newline) and ``\\b`` (backslash). The latter is common
        on Windows output paths.

    Returns ``None`` for lines without a separate real path (relative to
    ``RUNFILES_DIR``, not useful in manifest-only mode).
    """
    if line.startswith(" "):
        parts = line[1:].split(" ", 1)
        if len(parts) != 2:
            return None
        # Unescape in the same order Bazel does; \b last so a literal backslash
        # written as \b is not mistaken for another escape.
        return parts[1].replace(r"\s", " ").replace(r"\n", "\n").replace(r"\b", "\\")
    parts = line.split(" ", 1)
    if len(parts) != 2:
        return None
    return parts[1]


@callonce
def _bazel_manifest_site_packages() -> frozenset[str]:
    """Resolved ``site-packages`` dirs referenced by ``RUNFILES_MANIFEST_FILE``.

    In manifest mode (``RUNFILES_MANIFEST_FILE`` set, ``RUNFILES_DIR`` unset) the
    dependency dirs are real filesystem paths that need not live under a
    ``*.runfiles`` tree, so the ``.runfiles`` ancestor heuristic misses them.
    Parse the manifest's real-path column for ``site-packages`` components and
    collect those dirs so they can be recognized as genuine runfiles roots.
    """
    manifest = env.get("RUNFILES_MANIFEST_FILE")
    if not manifest:
        return frozenset()
    marker = "/site-packages/"
    raw_roots: set[str] = set()
    try:
        with open(manifest, encoding="utf-8", errors="surrogateescape") as f:
            for raw in f:
                real = _manifest_real_path(raw.rstrip("\n"))
                if real is None:
                    continue

                # On Windows the manifest real path uses backslash separators,
                # so normalize before searching for the site-packages marker.
                real = real.replace("\\", "/")
                idx = real.find(marker)
                if idx != -1:
                    raw_roots.add(real[: idx + len(marker) - 1])
    except OSError:
        return frozenset()

    resolved: set[str] = set()
    for r in raw_roots:
        try:
            resolved.add(str(Path(r).resolve()))
        except OSError:
            resolved.add(r)
    return frozenset(resolved)


def _is_bazel_runfiles_dir(path: Path) -> bool:
    """Whether ``path`` is a dependency dir inside the Bazel runfiles tree.

    Returns ``False`` when not running under Bazel so the Bazel-only directory
    scan never runs against an arbitrary (possibly shared) dir whose lone
    ``.dist-info`` would otherwise capture unrelated sibling modules. Under
    Bazel a dir qualifies if it is below ``RUNFILES_DIR``, has a ``*.runfiles``
    ancestor, or is a manifest-listed ``site-packages`` real path.
    """
    if not _in_bazel():
        return False
    runfiles_dir = env.get("RUNFILES_DIR")
    if runfiles_dir:
        try:
            path.relative_to(runfiles_dir)
            return True
        except ValueError:
            pass
    if any(parent.suffix == ".runfiles" for parent in path.parents):
        return True
    manifest_roots = _bazel_manifest_site_packages()
    if not manifest_roots:
        return False
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    return resolved in manifest_roots


@callonce
def _package_for_root_module_mapping() -> t.Optional[dict[str, Distribution]]:
    import importlib.metadata as importlib_metadata

    namespaces: dict[str, bool] = {}

    def is_namespace(f: importlib_metadata.PackagePath):
        root = f.parts[0]
        try:
            return namespaces[root]
        except KeyError:
            pass

        if len(f.parts) < 2:
            namespaces[root] = False
            return False

        located_f = t.cast(Path, f.locate())
        parent = located_f.parents[len(f.parts) - 2]
        if parent.is_dir() and not (parent / "__init__.py").exists():
            namespaces[root] = True
            return True

        namespaces[root] = False
        return False

    try:
        dists = list(importlib_metadata.distributions())
    except Exception:
        LOG.warning(
            "Unable to enumerate installed distributions, "
            "please report this to https://github.com/DataDog/dd-trace-py/issues",
            exc_info=True,
        )
        return None

    # AIDEV-NOTE: per-dist try/except -- one bad dist used to collapse the whole
    # mapping to None (silently breaking is_third_party for the rest of the process).
    mapping: dict[str, Distribution] = {}
    no_files_dists: list[importlib_metadata.Distribution] = []

    d: t.Optional[Distribution] = None
    for dist in dists:
        try:
            if not (files := dist.files):
                no_files_dists.append(dist)
                continue
            metadata = dist.metadata
            name = metadata["name"]
            version = metadata["version"]
            if not (name and version):
                continue
            d = Distribution(name=name, version=version)
            for f in files:
                root = f.parts[0]
                if root.endswith(".dist-info") or root.endswith(".egg-info") or root == "..":
                    continue
                if is_namespace(f):
                    root = "/".join(f.parts[:2])
                if root not in mapping:
                    mapping[root] = d
        except Exception as exc:
            _warn_bad_dist(dist, exc)

    # Fallback for environments where RECORD files are absent (e.g. Bazel's
    # rules_python, which deliberately omits RECORD for hermeticity).
    #
    # Strategy 1: packages_distributions: fast, covers most packages.
    # Strategy 2: directory scan of the dist's isolated site-packages: covers
    #   the remainder, but only when the site-packages dir contains exactly one
    #   .dist-info (true in Bazel's per-package isolated layout; unsafe in a
    #   normal shared site-packages where multiple dists share one directory).
    if no_files_dists:
        # Strategy 1: packages_distributions maps module_name -> [dist_name, ...]
        # A single no-RECORD / malformed dist can make packages_distributions()
        # raise; that must not abort the whole fallback and leave good packages
        # unresolved (breaking is_third_party / code provenance). Degrade to an
        # empty Strategy 1 result and let the directory scan (Strategy 2) run.
        try:
            pkg_dists = get_package_distributions()
        except Exception:
            LOG.debug("packages_distributions() failed; falling back to directory scan", exc_info=True)
            pkg_dists = {}

        # Invert to dist_name_lower -> Distribution (lazily, only for no-files dists).
        name_to_dist_obj: dict[str, Distribution] = {}
        for _dist in no_files_dists:
            try:
                _n = _dist.metadata["name"]
                _v = _dist.metadata["version"]
                if _n and _v:
                    name_to_dist_obj[_n.lower()] = Distribution(name=_n, version=_v)
            except Exception as exc:
                _warn_bad_dist(_dist, exc)

        for module_name, dist_names in pkg_dists.items():
            if module_name in mapping:
                continue

            resolved: list[Distribution] = []
            for dist_name in dist_names:
                if not dist_name:
                    continue
                d = name_to_dist_obj.get(dist_name.lower())
                if d is not None and d not in resolved:
                    resolved.append(d)

            # Skip ambiguous roots provided by more than one no-RECORD dist:
            # mapping such a top-level module to a single dist would let code
            # provenance attribute another dist's files to the chosen one.
            if len(resolved) != 1:
                continue
            mapping[module_name] = resolved[0]

        # Strategy 2: directory scan of each no-RECORD dist's isolated
        # site-packages dir, gated to verified Bazel runfiles dependency dirs.
        for _dist in no_files_dists:
            try:
                _n = _dist.metadata["name"]
                _v = _dist.metadata["version"]
                if not (_n and _v):
                    continue
                site_packages = t.cast(Path, _dist.locate_file(""))
                # Gate the scan to verified Bazel runfiles dependency dirs. The
                # heuristic below assumes Bazel's per-package isolated layout
                # (one dist owning the whole dir); outside runfiles a minimal or
                # layered site-packages can hold this dist's lone .dist-info plus
                # *unrelated* top-level code, which the scan would then wrongly
                # attribute to this dist (marking those app frames third-party).
                if not _is_bazel_runfiles_dir(site_packages):
                    continue
                # Safety guard: only treat this as an isolated site-packages if it
                # contains exactly one .dist-info directory (Bazel's per-package
                # layout guarantee). A shared site-packages has many .dist-info dirs
                # and scanning it would produce wildly incorrect mappings.
                dist_infos = (
                    [child for child in site_packages.iterdir() if child.suffix == ".dist-info"]
                    if site_packages.is_dir()
                    else []
                )
                if len(dist_infos) != 1:
                    continue
                # The lone .dist-info must be _dist's own metadata, not an
                # unrelated dist that merely happens to be alone in a minimal
                # shared dir (e.g. _dist is an .egg-info/editable install).
                # Otherwise we would attribute that dir's unrelated modules to
                # _dist. Compare against _dist's metadata dir name.
                own_meta = getattr(_dist, "_path", None)
                if own_meta is None or dist_infos[0].name != Path(own_meta).name:
                    continue
                d = Distribution(name=_n, version=_v)
                for child in site_packages.iterdir():
                    root = child.name
                    if child.suffix in (".dist-info", ".egg-info") or root == ".." or root.startswith("__"):
                        continue
                    if root not in mapping:
                        mapping[root] = d
            except Exception as exc:
                _warn_bad_dist(_dist, exc)

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


def _packages_distributions() -> t.Mapping[str, list[str]]:
    """
    Return a mapping of top-level packages to their
    distributions.
    >>> import collections.abc
    >>> pkgs = packages_distributions()
    >>> all(isinstance(dist, collections.abc.Sequence) for dist in pkgs.values())
    True
    """
    import importlib.metadata as importlib_metadata

    pkg_to_dist = collections.defaultdict(list)
    for dist in importlib_metadata.distributions():
        try:
            name = dist.metadata["Name"]
            if not name:
                continue
            for pkg in _top_level_declared(dist) or _top_level_inferred(dist):
                pkg_to_dist[pkg].append(name)
        except Exception as exc:
            _warn_bad_dist(dist, exc)
    return dict(pkg_to_dist)


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


def _top_level_inferred(dist):
    opt_names = set(map(_get_toplevel_name, _always_iterable(dist.files)))

    def importable_name(name):
        return "." not in name

    return filter(importable_name, opt_names)


# copied from more_itertools 8.8
def _always_iterable(obj, base_type=(str, bytes)):
    """If *obj* is iterable, return an iterator over its items::
        >>> obj = (1, 2, 3)
        >>> list(always_iterable(obj))
        [1, 2, 3]
    If *obj* is not iterable, return a one-item iterable containing *obj*::
        >>> obj = 1
        >>> list(always_iterable(obj))
        [1]
    If *obj* is ``None``, return an empty iterable:
        >>> obj = None
        >>> list(always_iterable(None))
        []
    By default, binary and text strings are not considered iterable::
        >>> obj = 'foo'
        >>> list(always_iterable(obj))
        ['foo']
    If *base_type* is set, objects for which ``isinstance(obj, base_type)``
    returns ``True`` won't be considered iterable.
        >>> obj = {'a': 1}
        >>> list(always_iterable(obj))  # Iterate over the dict's keys
        ['a']
        >>> list(always_iterable(obj, base_type=dict))  # Treat dicts as a unit
        [{'a': 1}]
    Set *base_type* to ``None`` to avoid any special handling and treat objects
    Python considers iterable as iterable:
        >>> obj = 'foo'
        >>> list(always_iterable(obj, base_type=None))
        ['f', 'o', 'o']
    """
    if obj is None:
        return iter(())

    if (base_type is not None) and isinstance(obj, base_type):
        return iter((obj,))

    try:
        return iter(obj)
    except TypeError:
        return iter((obj,))

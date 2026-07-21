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

# AIDEV-NOTE: dist.metadata access is per-dist defensive — malformed METADATA
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


def _in_bazel() -> bool:
    """Whether the process is running under Bazel runfiles (dir or manifest mode)."""
    return bool(env.get("RUNFILES_DIR") or env.get("RUNFILES_MANIFEST_FILE"))


def _manifest_real_path(line: str) -> t.Optional[str]:
    r"""The real path from one Bazel runfiles MANIFEST line.

    Two formats exist:
      - plain '<runfiles_path> <real_path>' (we split on the first space)
      - an escaped form used when either path contains a space, newline, or
        backslash. The line starts with a space and both fields escape \s
        (space), \n (newline) and \b (backslash).

    Returns None for lines without a separate real path.
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
    """Resolved site-packages dirs referenced by RUNFILES_MANIFEST_FILE.

    In manifest mode (when RUNFILES_MANIFEST_FILE is set, RUNFILES_DIR is unset)
    dependency dirs are real filesystem paths that are not necessarily under a
    *.runfiles tree, so the .runfiles ancestor heuristic misses them.
    Parse the manifest's real-path column for site-packages components and
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

                # On Windows, the manifest real path uses backslash separators,
                # so normalize before searching for the site-packages marker.
                real = real.replace("\\", "/")
                # A single path can contain more than one "/site-packages/"
                # component (e.g. an outer directory literally named
                # site-packages before the real dependency root at
                # .../dep/site-packages/pkg.py). Record every candidate
                # root rather than only the first, so the innermost dependency
                # site-packages is recognized too.
                start = 0
                while (idx := real.find(marker, start)) != -1:
                    raw_roots.add(real[: idx + len(marker) - 1])
                    start = idx + 1
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
    """Checks whether path is a dependency dir inside the Bazel runfiles tree.

    Returns False when not running under Bazel so the Bazel-only directory
    scan never runs against an arbitrary (possibly shared) dir whose lone
    .dist-info would otherwise capture unrelated sibling modules. Under
    Bazel a dir qualifies if it is below RUNFILES_DIR, has a *.runfiles
    ancestor, or is a manifest-listed site-packages real path (directly, or
    via a binary venv whose entries symlink into that real root).
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

    if resolved in manifest_roots:
        return True

    # rules_python can expose dependencies through a binary venv whose
    # site-packages holds symlinks into the real repository site-packages.
    # importlib.metadata then discovers the dist under that virtual dir, which
    # is not itself a symlink and so is absent from the manifest's real-path
    # roots. Follow a symlinked entry to its real parent and accept the dir
    # when that parent is a manifest-listed site-packages root.
    try:
        for child in path.iterdir():
            if not child.is_symlink():
                continue
            try:
                real_parent = str(child.resolve().parent)
            except OSError:
                continue
            if real_parent in manifest_roots:
                return True
    except OSError:
        pass

    return False


def _is_namespace_module(base: Path, module_name: str) -> bool:
    """Whether ``base/module_name`` is a namespace package (dir, no __init__.py).

    Used by the no-RECORD fallback to avoid pinning a shared namespace
    top-level (e.g. ``google``) to a single distribution. A regular package
    (has ``__init__.py``) or a single-file module is not a namespace.
    """
    pkg_dir = base / module_name
    try:
        return pkg_dir.is_dir() and not (pkg_dir / "__init__.py").exists()
    except OSError:
        return False


# Bound the recursion that walks nested no-RECORD namespace packages, so a
# pathological/deep tree (or symlink loop) cannot stall the mapping build.
_NAMESPACE_SCAN_MAX_DEPTH = 16


def _add_namespace_subentries(
    ns_dir: Path,
    prefix: str,
    dist: Distribution,
    mapping: dict[str, Distribution],
    depth: int = 0,
) -> None:
    """Recursively map sub-entries of a no-RECORD namespace package.

    Two distributions can share intermediate namespace levels: e.g.
    ``google-cloud-storage`` ships ``google/cloud/storage`` and
    ``google-cloud-bigquery`` ships ``google/cloud/bigquery``, both under the
    ``google/cloud`` namespace. Keying only one level deep collapses both to
    ``google/cloud`` and drops the second dist, so ``CodeProvenance`` records
    only the first runfiles path and the other dependency's frames stay
    classified as user code. We descend through nested namespace dirs (those
    without ``__init__.py``) until we reach a concrete package (has
    ``__init__.py``) or a module file, mapping a key at each level so every
    distribution keeps at least one distinct entry and its path is recorded.

    A nested namespace level (a directory without ``__init__.py``) is itself
    shareable, so it is NOT mapped to ``dist``; only concrete packages (with
    ``__init__.py``) and module files are. We descend through the bare
    namespace dirs so each distribution's concrete leaf gets its own
    dist-specific key, and ``filename_to_package`` then resolves files by the
    deepest matching prefix.
    """
    if depth >= _NAMESPACE_SCAN_MAX_DEPTH:
        return
    try:
        children = list(ns_dir.iterdir())
    except OSError:
        return
    for child in children:
        name = child.name
        if name.startswith("__"):
            continue
        is_dir = child.is_dir()
        is_module_file = child.suffix == ".py" and not is_dir
        if not (is_dir or is_module_file):
            continue
        key = f"{prefix}/{name}"
        if is_dir and not (child / "__init__.py").exists():
            # Shared intermediate namespace level: never pin it to a single
            # dist (that misattributes a sibling dist's files to whichever was
            # scanned first). Recurse so the concrete leaf below gets a
            # dist-specific key instead.
            _add_namespace_subentries(child, key, dist, mapping, depth + 1)
        elif key not in mapping:
            mapping[key] = dist


@callonce
def _package_for_root_module_mapping() -> t.Optional[dict[str, Distribution]]:
    import importlib.metadata as importlib_metadata

    # Cache per directory prefix whether it is a *regular* package (a directory
    # that ships an ``__init__.py``). PEP 420 namespace packages have no
    # ``__init__.py`` at their shared levels, so several distributions can
    # contribute siblings under the same prefix (``google/cloud/storage`` vs
    # ``google/cloud/bigquery``). The key must therefore be the deepest
    # importable root, not a fixed 2-level prefix, otherwise every sibling
    # collapses onto whichever dist was scanned first and the longest-prefix
    # lookup in filename_to_package has nothing specific to match.
    regular_pkg: dict[str, bool] = {}

    def root_key(f: importlib_metadata.PackagePath) -> str:
        parts = f.parts
        n = len(parts)
        if n < 2:
            # Top-level module file (e.g. ``six.py``); keep the file name.
            return parts[0]

        located: t.Optional[Path] = None
        for depth in range(1, n):
            prefix = "/".join(parts[:depth])
            is_regular = regular_pkg.get(prefix)
            if is_regular is None:
                if located is None:
                    located = t.cast(Path, f.locate())
                pkg_dir = located.parents[n - 1 - depth]
                is_regular = pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists()
                regular_pkg[prefix] = is_regular
            if is_regular:
                # First regular package on the path: this is the import root.
                return prefix

        # Every directory level is a namespace (no __init__.py anywhere on the
        # path). Two distributions can then contribute module files directly
        # under the shared namespace (dist A ships ``acme/foo.py``, dist B ships
        # ``acme/bar.py``); dropping the file name collapses both onto the bare
        # ``acme`` key and attributes every sibling to whichever dist was
        # scanned first. Keep the full path (including the file name) so each
        # module gets a distinct key the longest-prefix lookup can match.
        return "/".join(parts)

    try:
        dists = list(importlib_metadata.distributions())
    except Exception:
        LOG.warning(
            "Unable to enumerate installed distributions, "
            "please report this to https://github.com/DataDog/dd-trace-py/issues",
            exc_info=True,
        )
        return None

    # AIDEV-NOTE: per-dist try/except — one bad dist used to collapse the whole
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
                key = root_key(f)
                if key not in mapping:
                    mapping[key] = d
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
        # Top-level roots that the directory scan sees as namespace packages
        # (no __init__.py) in any no-RECORD dist. A bare namespace root is
        # inherently shareable across dists, so it must never be mapped to a
        # single dist -- even when packages_distributions() resolves only one
        # (the other dist may omit top_level.txt and so be invisible to it).
        # Collected during Strategy 2 and stripped from the mapping afterwards.
        namespace_roots: set[str] = set()

        # Roots that some no-RECORD dist regularly owns (has __init__.py or a
        # top-level module file). A regular owner always wins the bare root over
        # a shared namespace.
        regular_owners: dict[str, Distribution] = {}

        # Roots that the packages_distributions() fallback (Strategy 1) added.
        # Only these may be stripped as shared namespace roots; a RECORD-backed
        # or regular-package owner of the same root must be preserved.
        strategy1_added: set[str] = set()

        # Invert to dist_name_lower -> Distribution and its base dir (lazily,
        # only for no-files dists). The base dir lets Strategy 1 tell a bare
        # namespace top-level from a regular package on its own.
        name_to_dist_obj: dict[str, Distribution] = {}
        name_to_base: dict[str, Path] = {}
        for _dist in no_files_dists:
            try:
                _n = _dist.metadata["name"]
                _v = _dist.metadata["version"]
                if _n and _v:
                    name_to_dist_obj[_n.lower()] = Distribution(name=_n, version=_v)
                    try:
                        name_to_base[_n.lower()] = t.cast(Path, _dist.locate_file(""))
                    except Exception:  # nosec - base dir is best-effort
                        pass
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

            # Skip ambiguous namespace roots: when a top-level module (e.g.
            # "google") is provided by more than one no-RECORD distribution,
            # mapping it to a single dist would let code provenance attribute
            # another dist's files to the chosen one (it resolves the root via
            # the first matching sys.path entry). Rely on the path-aware
            # sub-entries added by the directory scan (Strategy 2) instead.
            if len(resolved) != 1:
                continue
            # Also skip a bare namespace top-level even when a single dist
            # reports it: a sibling namespace dist may omit top_level.txt (and
            # so be invisible here), and the Strategy 2 scan that would strip
            # this root is skipped outside Bazel. Pinning it would make the
            # longest-prefix lookup attribute the sibling's "google/<other>/..."
            # files to this dist. The directory scan still adds the path-aware
            # sub-entries (e.g. "google/cloud") when it does run.
            base = name_to_base.get(resolved[0].name.lower())
            if base is not None and _is_namespace_module(base, module_name):
                continue
            mapping[module_name] = resolved[0]
            strategy1_added.add(module_name)

        # Strategy 2: directory scan. We do NOT skip dists already covered by
        # Strategy 1: namespace packages get only their top-level module mapped
        # there (e.g. "google"), but _root_module returns sub-entries like
        # "google/cloud", which only the directory scan below can add.
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
                    is_namespace_pkg = child.is_dir() and not (child / "__init__.py").exists()
                    if not is_namespace_pkg:
                        # Regular package/module (has __init__.py, or a module
                        # file): the whole top-level dir/file belongs to this
                        # dist, so it is the legitimate owner of the bare root.
                        regular_owners.setdefault(root, d)
                        if root not in mapping:
                            mapping[root] = d
                        continue
                    # Namespace packages (no __init__.py) must NOT map their bare
                    # top-level root (e.g. "google"): it is shared across dists,
                    # so attributing it to one would misclassify another dist's
                    # files. Add the path-aware sub-entries instead, descending
                    # through nested namespace levels so that dists sharing an
                    # intermediate level (e.g. "google/cloud") still each get a
                    # distinct, recorded key.
                    namespace_roots.add(root)
                    _add_namespace_subentries(child, root, d, mapping)
            except Exception as exc:
                _warn_bad_dist(_dist, exc)

        # Reconcile bare namespace roots that Strategy 1 mapped to a single
        # dist. The directory scan is authoritative about ownership:
        #   - If some dist regularly owns the root (has __init__.py or a module
        #     file), the bare root belongs to that regular owner; point the
        #     Strategy 1 entry at it (it may have guessed a namespace dist).
        #   - Otherwise the root is purely a shared namespace; drop the bare
        #     entry so only the path-aware sub-entries (e.g. "google/cloud")
        #     remain and no dist's "google" dir is recorded under another.
        # We only touch Strategy 1's own additions, so a RECORD-backed owner
        # (mapped by the main loop) is never disturbed.
        for ns_root_name in namespace_roots & strategy1_added:
            owner = regular_owners.get(ns_root_name)
            if owner is not None:
                mapping[ns_root_name] = owner
            else:
                mapping.pop(ns_root_name, None)

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

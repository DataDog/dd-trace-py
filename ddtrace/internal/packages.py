from functools import lru_cache as cached
from functools import singledispatch
import inspect
import logging
import sys
import sysconfig
from types import ModuleType
import typing as t

from ddtrace.ext.ci import _filter_sensitive_info
from ddtrace.internal.compat import Path
from ddtrace.internal.module import origin
from ddtrace.internal.utils.cache import callonce
from ddtrace.settings.third_party import config as tp_config


LOG = logging.getLogger(__name__)


Distribution = t.NamedTuple(
    "Distribution", [("name", str), ("version", str), ("repository_url", str), ("commit_sha", str)]
)

# Mapping from distribution name to Distribution
_DISTRIBUTIONS: t.Optional[t.Dict[str, Distribution]] = None
# Mapping from top level module/import package names to the names of distribution packages
_PACKAGE_DISTRIBUTIONS: t.Optional[t.Mapping[str, t.List[str]]] = None
# Mapping root module to Distribution it belongs
_ROOT_TO_PACKAGE: t.Optional[t.Dict[str, str]] = None


def parse_git_metadata(project_urls: t.List[str]) -> t.Tuple[str, str]:
    source_code_link = ""
    for val in project_urls:
        capt_val = val.split(", ")
        if len(capt_val) > 1 and capt_val[0] == "source_code_link":
            source_code_link = capt_val[1].strip()
            break

    if source_code_link and "#" in source_code_link:
        repository_url, commit_sha = source_code_link.split("#")
        commit_sha = commit_sha.split("&")[0]
        filtered_git_url = _filter_sensitive_info(repository_url)
        if type(filtered_git_url) != str:
            return "", commit_sha
        return filtered_git_url, commit_sha
    return "", ""


@callonce
def parse_importlib_metadata():
    """parses importlib.metadata and populate distribution data structures to be used"""
    global _DISTRIBUTIONS
    global _PACKAGE_DISTRIBUTIONS
    global _ROOT_TO_PACKAGE

    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata

    namespaces: t.Dict[str, bool] = {}

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

    for dist in importlib_metadata.distributions():
        # PKG-INFO and/or METADATA files are parsed when dist.metadata is accessed
        # Optimization: we should avoid accessing dist.metadata more than once
        metadata = dist.metadata
        name = metadata["name"]
        version = metadata["version"]

        project_urls = metadata.get_all("Project-URL")
        filtered_git_url, commit_sha = parse_git_metadata(project_urls)

        _DISTRIBUTIONS[name] = Distribution(
            name=name, version=version, repository_url=filtered_git_url, commit_sha=commit_sha
        )

        # populate _PACKAGE_DISTRIBUTIONS
        for pkg in _top_level_declared(dist) or _top_level_inferred(dist):
            _PACKAGE_DISTRIBUTIONS[pkg].append(name)

        # populate _ROOT_TO_PACKAGE
        files = dist.files or []
        for f in files:
            root = f.parts[0]
            if root.endswith(".dist-info") or root.endswith(".egg-info") or root == "..":
                continue
            if is_namespace(f):
                root = "/".join(f.parts[:2])
            if root not in _ROOT_TO_PACKAGE:
                _ROOT_TO_PACKAGE[root] = name


def get_distribution(name: str) -> t.Optional[Distribution]:
    parse_importlib_metadata()
    return _DISTRIBUTIONS.get(name, None) if _DISTRIBUTIONS else None


@callonce
def get_distributions():
    # type: () -> t.Set[Distribution]
    """returns the name and version of all distributions in a python path"""
    parse_importlib_metadata()
    return set(_DISTRIBUTIONS.values()) if _DISTRIBUTIONS else set()


def get_package_distributions() -> t.Mapping[str, t.List[str]]:
    """a mapping of importable package names to their distribution name(s)"""
    parse_importlib_metadata()
    return _PACKAGE_DISTRIBUTIONS if _PACKAGE_DISTRIBUTIONS else {}


@cached(maxsize=1024)
def get_module_distribution_versions(module_name: str) -> t.Optional[t.Tuple[str, str]]:
    parse_importlib_metadata()
    if not module_name:
        return None

    names: t.List[str] = []
    pkgs = get_package_distributions()
    while names == []:
        try:
            if _DISTRIBUTIONS:
                package = _DISTRIBUTIONS[module_name]
                name = package.name
                version = package.version
                if name and version:
                    return (name, version)
        except Exception:  # nosec
            pass
        names = pkgs.get(module_name, [])
        if not names:
            # try to resolve the parent package
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


@cached(maxsize=256)
def get_version_for_package(name):
    # type: (str) -> str
    """returns the version of a package"""
    parse_importlib_metadata()
    if not _DISTRIBUTIONS:
        return ""

    return _DISTRIBUTIONS[name].version if name in _DISTRIBUTIONS else ""


def _effective_root(rel_path: Path, parent: Path) -> str:
    base = rel_path.parts[0]
    root = parent / base
    return base if root.is_dir() and (root / "__init__.py").exists() else "/".join(rel_path.parts[:2])


def _root_module(path: Path) -> str:
    # Try the most likely prefixes first
    for parent_path in (purelib_path, platlib_path):
        try:
            return _effective_root(path.relative_to(parent_path), parent_path)
        except ValueError:
            # Not relative to this path
            pass

    # Try to resolve the root module using sys.path. We keep the shortest
    # relative path as the one more likely to give us the root module.
    min_relative_path = max_parent_path = None
    for parent_path in (Path(_).resolve() for _ in sys.path):
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
    # /some/path.runfiles/.../site-packages/<root_module>/...
    if any(p.suffix == ".runfiles" for p in path.parents):
        for s in path.parents:
            if s.parent.name == "site-packages":
                return s.name

    msg = f"Could not find root module for path {path}"
    raise ValueError(msg)


def _package_for_root_module_mapping() -> t.Optional[t.Dict[str, str]]:
    parse_importlib_metadata()
    return _ROOT_TO_PACKAGE if _ROOT_TO_PACKAGE else {}


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
    parse_importlib_metadata()
    mapping = _package_for_root_module_mapping()
    if not mapping or not _DISTRIBUTIONS:
        return None

    path = Path(filename) if isinstance(filename, str) else filename
    root_module = _root_module(path.resolve())

    if root_module in mapping:
        return _DISTRIBUTIONS.get(mapping[root_module], None)
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
    rpath = path.resolve()

    return (rpath.is_relative_to(stdlib_path) or rpath.is_relative_to(platstdlib_path)) and not (
        rpath.is_relative_to(purelib_path) or rpath.is_relative_to(platlib_path)
    )


@cached(maxsize=256)
def is_third_party(path: Path) -> bool:
    package = filename_to_package(str(path))
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
    parse_importlib_metadata()
    """Determine if a distribution is available in the current environment."""
    if not _DISTRIBUTIONS:
        return False
    return name in _DISTRIBUTIONS


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
        inspect.getmodulename(name)
        or str(name)
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

import collections
from functools import lru_cache as cached
from functools import singledispatch
import inspect
import logging
import sys
import sysconfig
from types import ModuleType
import typing as t

from ddtrace.internal.compat import Path
from ddtrace.internal.module import origin
from ddtrace.internal.utils.cache import callonce
from ddtrace.settings.third_party import config as tp_config


LOG = logging.getLogger(__name__)


Distribution = t.NamedTuple("Distribution", [("name", str), ("version", str), ("paths", t.Optional[t.Tuple[str]])])

_PACKAGE_DISTRIBUTIONS: t.Optional[t.Mapping[str, t.List[str]]] = None


class TrieNode:
    def __init__(self) -> None:
        self.children: t.Dict[str, "TrieNode"] = collections.defaultdict(TrieNode)
        self.library: t.Optional[str] = None

    def insert(self, path_parts: t.List[str], library: str) -> None:
        node = self
        for part in path_parts:
            node = node.children[part]
        node.library = library

    def collapse(self) -> None:
        def _collapse(node: "TrieNode") -> t.Optional[str]:
            if not node.children:
                return node.library

            child_libs = []
            for child in node.children.values():
                lib = _collapse(child)
                child_libs.append(lib)

            unique_libs = set(child_libs)
            if len(unique_libs) == 1 and list(unique_libs)[0] is not None:
                node.children.clear()
                node.library = unique_libs.pop()
                return node.library

            return None

        _collapse(self)

    def collect_library_paths(
        self, current_path: Path = Path(), result: t.Optional[t.Dict[str, t.Set[str]]] = None
    ) -> t.Dict[str, t.Set[str]]:
        """Returns: {library: set of collapsed path prefixes as string paths}"""
        if result is None:
            result = collections.defaultdict(set)

        if self.library:
            result[self.library].add(str(current_path))
            return result

        for part, child in self.children.items():
            child.collect_library_paths(current_path / part, result)

        return result


def get_py_files(dist):
    """ "Retrieve .py files in distribution"""
    files = dist.files or []
    py_files = [file for file in files if file.name.endswith(".py") and not file.name.startswith("__editable__")]
    return py_files


def get_distributions():
    # type: () -> t.Set[Distribution]
    """returns the name and version of all distributions in a python path"""
    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    names_to_versions = {}
    trie = TrieNode()
    for dist in importlib_metadata.distributions():
        # NOTE: if you ever want to use any field in the metadata, you must
        # access it using below metadata object, otherwise we'd be parsing the
        # metadata file again which would increase the runtime of this function
        # significantly
        metadata = dist.metadata
        name = metadata["name"]
        version = metadata["version"]
        if name and version:
            names_to_versions[name] = version
        paths = get_py_files(dist)
        for path in paths:
            trie.insert(path.parts, name)
    trie.collapse()

    paths = trie.collect_library_paths(purelib_path)

    pkgs = set()
    for name, version in names_to_versions.items():
        dist_paths = tuple(paths.get(name, []))
        pkgs.add(Distribution(paths=dist_paths, name=name, version=version))

    return pkgs


def get_package_distributions() -> t.Mapping[str, t.List[str]]:
    """a mapping of importable package names to their distribution name(s)"""
    global _PACKAGE_DISTRIBUTIONS
    if _PACKAGE_DISTRIBUTIONS is None:
        try:
            import importlib.metadata as importlib_metadata
        except ImportError:
            import importlib_metadata  # type: ignore[no-redef]

        # Prefer the official API if available, otherwise fallback to the vendored version
        if hasattr(importlib_metadata, "packages_distributions"):
            _PACKAGE_DISTRIBUTIONS = importlib_metadata.packages_distributions()
        else:
            _PACKAGE_DISTRIBUTIONS = _packages_distributions()
    return _PACKAGE_DISTRIBUTIONS


@cached(maxsize=1024)
def get_module_distribution_versions(module_name: str) -> t.Optional[t.Tuple[str, str]]:
    if not module_name:
        return None
    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    names: t.List[str] = []
    pkgs = get_package_distributions()
    while names == []:
        try:
            package = importlib_metadata.distribution(module_name)
            metadata = package.metadata
            name = metadata["name"]
            version = metadata["version"]
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
    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    try:
        return importlib_metadata.version(name)
    except Exception:
        return ""


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


@callonce
def _package_for_root_module_mapping() -> t.Optional[t.Dict[str, Distribution]]:
    try:
        import importlib.metadata as metadata
    except ImportError:
        import importlib_metadata as metadata  # type: ignore[no-redef]

    namespaces: t.Dict[str, bool] = {}

    def is_namespace(f: metadata.PackagePath):
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
        mapping = {}

        for dist in metadata.distributions():
            if dist is not None and dist.files is not None:
                d = Distribution(name=dist.metadata["name"], version=dist.version, paths=None)
                for f in dist.files:
                    root = f.parts[0]
                    if root.endswith(".dist-info") or root.endswith(".egg-info") or root == "..":
                        continue
                    if is_namespace(f):
                        root = "/".join(f.parts[:2])
                    if root not in mapping:
                        mapping[root] = d

        return mapping

    except Exception:
        LOG.warning(
            "Unable to build package file mapping, "
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
        return mapping.get(_root_module(path.resolve()))
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
    """Determine if a distribution is available in the current environment."""
    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    try:
        importlib_metadata.distribution(name)
    except importlib_metadata.PackageNotFoundError:
        return False

    return True


# ----
# the below helpers are copied from importlib_metadata
# ----


def _packages_distributions() -> t.Mapping[str, t.List[str]]:
    """
    Return a mapping of top-level packages to their
    distributions.
    >>> import collections.abc
    >>> pkgs = packages_distributions()
    >>> all(isinstance(dist, collections.abc.Sequence) for dist in pkgs.values())
    True
    """
    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    pkg_to_dist = collections.defaultdict(list)
    for dist in importlib_metadata.distributions():
        for pkg in _top_level_declared(dist) or _top_level_inferred(dist):
            pkg_to_dist[pkg].append(dist.metadata["Name"])
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

import logging
import os
import sys
import sysconfig
from types import ModuleType
import typing as t

from ddtrace.internal.compat import Path
from ddtrace.internal.module import origin
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.utils.cache import callonce
from ddtrace.settings.third_party import config as tp_config


LOG = logging.getLogger(__name__)


try:
    fspath = os.fspath
except AttributeError:
    # Stolen from Python 3.10
    def fspath(path):
        # For testing purposes, make sure the function is available when the C
        # implementation exists.
        """Return the path representation of a path-like object.

        If str or bytes is passed in, it is returned unchanged. Otherwise the
        os.PathLike interface is used to get the path representation. If the
        path representation is not str or bytes, TypeError is raised. If the
        provided path is not str, bytes, or os.PathLike, TypeError is raised.
        """
        if isinstance(path, (str, bytes)):
            return path

        # Work from the object's type to match method resolution of other magic
        # methods.
        path_type = type(path)
        try:
            path_repr = path_type.__fspath__(path)
        except AttributeError:
            if hasattr(path_type, "__fspath__"):
                raise
            else:
                raise TypeError("expected str, bytes or os.PathLike object, not " + path_type.__name__)
        if isinstance(path_repr, (str, bytes)):
            return path_repr
        raise TypeError(
            "expected {}.__fspath__() to return str or bytes, "
            "not {}".format(path_type.__name__, type(path_repr).__name__)
        )


Distribution = t.NamedTuple("Distribution", [("name", str), ("version", str), ("path", t.Optional[str])])


@callonce
def get_distributions():
    # type: () -> t.Set[Distribution]
    """returns the name and version of all distributions in a python path"""
    try:
        import importlib.metadata as importlib_metadata
    except ImportError:
        import importlib_metadata  # type: ignore[no-redef]

    pkgs = set()
    for dist in importlib_metadata.distributions():
        # Get the root path of all files in a distribution
        path = str(dist.locate_file(""))
        # PKG-INFO and/or METADATA files are parsed when dist.metadata is accessed
        # Optimization: we should avoid accessing dist.metadata more than once
        metadata = dist.metadata
        name = metadata["name"]
        version = metadata["version"]
        if name and version:
            pkgs.add(Distribution(path=path, name=name.lower(), version=version))

    return pkgs


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
                d = Distribution(name=dist.metadata["name"], version=dist.version, path=None)
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
    except ValueError:
        return None
    except OSError:
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


def is_third_party(path: Path) -> bool:
    package = filename_to_package(str(path))
    if package is None:
        return False

    return package.name in _third_party_packages()


def is_user_code(path: Path) -> bool:
    return not (is_stdlib(path) or is_third_party(path))


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

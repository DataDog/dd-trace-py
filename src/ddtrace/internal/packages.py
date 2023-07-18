import logging
import os
import sys
import typing as t

from ddtrace.internal.utils.cache import callonce


try:
    import pathlib
except ImportError:
    import pathlib2 as pathlib  # type: ignore[no-redef]


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

        # Hack for Python 3.5: there's no __fspath__ :(
        if sys.version_info[:2] == (3, 5) and isinstance(path, pathlib.Path):
            return str(path)

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
        else:
            raise TypeError(
                "expected {}.__fspath__() to return str or bytes, "
                "not {}".format(path_type.__name__, type(path_repr).__name__)
            )


# We don't store every file of every package but filter commonly used extensions
SUPPORTED_EXTENSIONS = (".py", ".so", ".dll", ".pyc")


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
            pkgs.add(Distribution(path=path, name=name, version=version))

    return pkgs


def _is_python_source_file(path):
    # type: (pathlib.PurePath) -> bool
    return os.path.splitext(path.name)[-1].lower() in SUPPORTED_EXTENSIONS


@callonce
def _package_file_mapping():
    # type: (...) -> t.Optional[t.Dict[str, Distribution]]
    try:
        import importlib.metadata as il_md
    except ImportError:
        import importlib_metadata as il_md  # type: ignore[no-redef]

    try:
        mapping = {}

        for ilmd_d in il_md.distributions():
            if ilmd_d is not None and ilmd_d.files is not None:
                d = Distribution(name=ilmd_d.metadata["name"], version=ilmd_d.version, path=None)
                for f in ilmd_d.files:
                    if _is_python_source_file(f):
                        mapping[fspath(f.locate())] = d

        return mapping

    except Exception:
        LOG.error(
            "Unable to build package file mapping, "
            "please report this to https://github.com/DataDog/dd-trace-py/issues",
            exc_info=True,
        )
        return None


def filename_to_package(filename):
    # type: (str) -> t.Optional[Distribution]
    mapping = _package_file_mapping()
    if mapping is None:
        return None

    if filename not in mapping and filename.endswith(".pyc"):
        # Replace .pyc by .py
        filename = filename[:-1]

    return mapping.get(filename)


def is_third_party(filename):
    # type: (str) -> bool
    return filename_to_package(filename) is not None

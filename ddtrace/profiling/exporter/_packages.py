# -*- encoding: utf-8 -*-
import enum
import logging
import os
import sys


try:
    import pathlib
except ImportError:
    import pathlib2 as pathlib  # type: ignore[no-redef]

try:
    import importlib.metadata as il_md
except ImportError:
    import importlib_metadata as il_md  # type: ignore[no-redef]

import typing


LOG = logging.getLogger(__name__)


try:
    fspath = os.fspath
except AttributeError:

    # Stolen from Python 3.10
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


Distribution = typing.NamedTuple("Distribution", [("name", str), ("version", str)])


def _is_python_source_file(
    path,  # type: pathlib.PurePath
):
    # type: (...) -> bool
    return os.path.splitext(path.name)[-1].lower() in SUPPORTED_EXTENSIONS


def _build_package_file_mapping():
    # type: (...) -> typing.Dict[str, Distribution]
    mapping = {}

    for ilmd_d in il_md.distributions():
        if ilmd_d is not None and ilmd_d.files is not None:
            d = Distribution(ilmd_d.metadata["name"], ilmd_d.version)
            for f in ilmd_d.files:
                if _is_python_source_file(f):
                    mapping[fspath(f.locate())] = d

    return mapping


class _load_status(enum.Enum):
    FAILED = "failed"


_FILE_PACKAGE_MAPPING = None  # type: typing.Optional[typing.Union[typing.Dict[str, Distribution], _load_status]]


def filename_to_package(
    filename,  # type: str
):
    # type: (...) -> typing.Optional[Distribution]
    global _FILE_PACKAGE_MAPPING
    if _FILE_PACKAGE_MAPPING is None:
        try:
            _FILE_PACKAGE_MAPPING = _build_package_file_mapping()
        except Exception:
            _FILE_PACKAGE_MAPPING = _load_status.FAILED
            LOG.error(
                "Unable to build package file mapping, "
                "please report this to https://github.com/DataDog/dd-trace-py/issues",
                exc_info=True,
            )
            return None
    elif _FILE_PACKAGE_MAPPING is _load_status.FAILED:
        return None

    if filename not in _FILE_PACKAGE_MAPPING and filename.endswith(".pyc"):
        # Replace .pyc by .py
        filename = filename[:-1]

    return _FILE_PACKAGE_MAPPING.get(filename)

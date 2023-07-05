import os

import mock
import pytest

from ddtrace.internal import packages
from ddtrace.internal.packages import get_distributions
from ddtrace.internal.packages import pathlib


def test_get_distributions():
    """use pkg_resources to validate package names and versions returned by get_distributions()"""
    import pkg_resources

    pkg_resources_ws = {pkg.project_name for pkg in pkg_resources.working_set}

    importlib_pkgs = set()
    for pkg in get_distributions():
        assert pkg.name
        assert pkg.version
        assert os.path.exists(pkg.path)
        # The package name in typing_extensions-4.x.x.dist-info/METADATA is set to `typing_extensions`
        # this is inconsistent with the package name found in pkg_resources. The block below corrects this.
        # The correct package name is typing-extensions.
        # The issue exists in pkgutil-resolve-name package.
        if pkg.name == "typing_extensions" and "typing-extensions" in pkg_resources_ws:
            importlib_pkgs.add("typing-extensions")
        elif pkg.name == "pkgutil_resolve_name" and "pkgutil-resolve-name" in pkg_resources_ws:
            importlib_pkgs.add("pkgutil-resolve-name")
        else:
            importlib_pkgs.add(pkg.name)

    # assert that pkg_resources and importlib.metadata return the same packages
    assert pkg_resources_ws == importlib_pkgs


@pytest.mark.parametrize(
    "filename,result",
    (
        ("toto.py", True),
        ("blabla/toto.py", True),
        ("/usr/blabla/toto.py", True),
        ("foo.pyc", True),
        ("/usr/foo.pyc", True),
        ("something", False),
        ("/something/", False),
        ("/something/nop", False),
        ("/something/yes.DLL", True),
    ),
)
def test_is_python_source_file(
    filename,  # type: str
    result,  # type: bool
):
    # type: (...) -> None
    assert packages._is_python_source_file(pathlib.Path(filename)) == result


@mock.patch.object(packages, "_is_python_source_file")
def test_filename_to_package_failure(_is_python_source_file):
    # type: (mock.MagicMock) -> None
    def _raise():
        raise RuntimeError("boom")

    _is_python_source_file.side_effect = _raise

    # type: (...) -> None
    assert packages.filename_to_package(packages.__file__) is None


def test_filename_to_package():
    # type: (...) -> None
    package = packages.filename_to_package(packages.__file__)
    assert package is None or package.name == "ddtrace"
    package = packages.filename_to_package(pytest.__file__)
    assert package is None or package.name == "pytest"

    import six

    package = packages.filename_to_package(six.__file__)
    assert package is None or package.name == "six"

    import google.protobuf.internal as gp

    package = packages.filename_to_package(gp.__file__)
    assert package is None or package.name == "protobuf"

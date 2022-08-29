import mock
import pytest

from ddtrace.profiling.exporter import _packages
from ddtrace.profiling.exporter._packages import pathlib


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
    assert _packages._is_python_source_file(pathlib.Path(filename)) == result


@mock.patch.object(_packages, "_build_package_file_mapping")
def test_filename_to_package_failure(
    build,  # type: mock.MagicMock
    caplog,  # type: pytest.LogCaptureFixture
):
    # type: (...) -> None
    _packages._FILE_PACKAGE_MAPPING = None

    def _raise():
        raise Exception

    build.side_effect = _raise

    # type: (...) -> None
    assert _packages.filename_to_package(_packages.__file__) is None
    assert len(caplog.records) == 1
    assert caplog.records[0].message == (
        "Unable to build package file mapping, please report this to https://github.com/DataDog/dd-trace-py/issues"
    )


def test_filename_to_package():
    # type: (...) -> None
    _packages._FILE_PACKAGE_MAPPING = None

    package = _packages.filename_to_package(_packages.__file__)
    assert package.name == "ddtrace"
    package = _packages.filename_to_package(pytest.__file__)
    assert package.name == "pytest"

    import six

    package = _packages.filename_to_package(six.__file__)
    assert package.name == "six"

    import google.protobuf.internal as gp

    package = _packages.filename_to_package(gp.__file__)
    assert package.name == "protobuf"

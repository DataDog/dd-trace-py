import sys

import mock

from ddtrace.version import get_version
from tests.tracer import _version  # noqa: F401 -> we need to import it so that it can be swapped with the test module


def test_get_version_from_version_file():
    with mock.patch.dict(sys.modules, {"ddtrace._version": sys.modules["tests.tracer._version"]}):
        assert get_version() == "my_test_version_from_generated_file"


def test_get_version_from_importlib_metadata():
    with mock.patch.dict(sys.modules, {"ddtrace._version": None}):
        version_str = "importlib.metadata.version"
        with mock.patch(version_str, return_value="my_test_version_from_import_lib") as mock_get_version:
            assert get_version() == "my_test_version_from_import_lib"
            mock_get_version.assert_called_with("ddtrace")


def test_get_version_dev_fallback():
    with mock.patch.dict(sys.modules, {"ddtrace._version": None}):
        version_str = "importlib.metadata.version"
        with mock.patch(version_str, side_effect=ModuleNotFoundError):
            assert get_version() == "dev"


class FakeDistributionIterator:
    def __init__(self, distribution):
        pass

    def __next__(self):
        raise StopIteration


class FakeDistribution:
    version = "my_test_version_from_pkg_resources"

    def __iter__(self):
        return FakeDistributionIterator(self)

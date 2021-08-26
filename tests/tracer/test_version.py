import sys

import mock
import pkg_resources

from ddtrace.version import get_version
from tests.tracer import _version  # noqa: F401 -> we need to import it so that it can be swapped with the test module


def test_get_version_from_version_file():
    with mock.patch.dict(sys.modules, {"ddtrace._version": sys.modules["tests.tracer._version"]}):
        assert get_version() == "my_test_version_from_generated_file"


def test_get_version_from_pkg_resources():
    with mock.patch.dict(sys.modules, {"ddtrace._version": None}):
        with mock.patch("pkg_resources.get_distribution") as mock_get_distribution:
            mock_get_distribution.return_value = FakeDistribution()
            assert get_version() == "my_test_version_from_pkg_resources"
            mock_get_distribution.assert_called_with("ddtrace.version")


def test_get_version_dev_fallback():
    with mock.patch.dict(sys.modules, {"ddtrace._version": None}):
        expected_error = pkg_resources.DistributionNotFound()
        with mock.patch("pkg_resources.get_distribution") as mock_get_distribution:
            mock_get_distribution.side_effect = expected_error
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

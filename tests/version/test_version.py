import sys

from ddtrace.version import get_version
from unittest import mock


class TestVersion:
    def test_get_version_from_version_file(self):
        from tests.version import _version
        with mock.patch.dict(sys.modules, {"ddtrace._version": sys.modules["tests.version._version"]}):
            assert get_version() == "my_test_version_from_generated_file"

    def test_get_version_from_pkg_resources(self):
        with mock.patch.dict(sys.modules, {"ddtrace._version": None}):
            with mock.patch("pkg_resources.get_distribution") as mock_get_distribution:
                mock_get_distribution.return_value = FakeDistribution()
                assert get_version() == "my_test_version_from_pkg_resources"
                mock_get_distribution.assert_called_with("ddtrace.version")

    def test_get_version_dev_fallback(self):
        with mock.patch.dict(sys.modules, {"ddtrace._version": None}):
            with mock.patch.dict(sys.modules, {"pkg_resources": None}):
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

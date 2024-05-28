import pytest

from ddtrace.contrib.pytest.constants import ITR_MIN_SUPPORTED_VERSION


def _get_pytest_version_tuple():
    if hasattr(pytest, "version_tuple"):
        return pytest.version_tuple
    return tuple(map(int, pytest.__version__.split(".")))


def _is_pytest_8_or_later():
    return _get_pytest_version_tuple() >= (8, 0, 0)


def _pytest_version_supports_itr():
    return _get_pytest_version_tuple() >= ITR_MIN_SUPPORTED_VERSION

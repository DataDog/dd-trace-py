import os.path
from unittest import mock

from ddtrace.internal.ci_visibility.utils import get_relative_or_absolute_path_for_path


def test_gets_relative_path():
    actual_output = get_relative_or_absolute_path_for_path("ddtrace/contrib", start_directory=os.getcwd())

    assert not os.path.isabs(actual_output)


def test_gets_absolute_path_with_exception():
    with mock.patch("os.path.relpath", side_effect=ValueError()):
        actual_output = get_relative_or_absolute_path_for_path("ddtrace/contrib", start_directory=os.getcwd())

        assert os.path.isabs(actual_output)

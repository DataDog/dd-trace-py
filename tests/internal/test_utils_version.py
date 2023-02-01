import re
import typing

import mock
import pytest

from ddtrace.internal.utils.version import _get_version_agent_format
from ddtrace.internal.utils.version import parse_version


def _assert_and_get_version_agent_format(version_agent_format):
    assert re.match(
        r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*["
        r"a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$",
        version_agent_format,
    )


@pytest.mark.parametrize(
    "version_str,expected",
    [
        ("5", (5, 0, 0)),
        ("0.5", (0, 5, 0)),
        ("0.5.0", (0, 5, 0)),
        ("1.0.0", (1, 0, 0)),
        ("1.2.0", (1, 2, 0)),
        ("1.2.8", (1, 2, 8)),
        ("2.0.0rc1", (2, 0, 0)),
        ("2.0.0-rc1", (2, 0, 0)),
        ("2.0.0 here be dragons", (2, 0, 0)),
        ("2020.6.19", (2020, 6, 19)),
        ("beta 1.0.0", (0, 0, 0)),
        ("no version found", (0, 0, 0)),
        ("", (0, 0, 0)),
    ],
)
def test_parse_version(version_str, expected):
    # type: (str, typing.Tuple[int, int, int]) -> None
    """Ensure parse_version helper properly parses versions"""
    assert parse_version(version_str) == expected


def test_default_version_agent_format():
    version_agent_format = _get_version_agent_format()
    _assert_and_get_version_agent_format(version_agent_format)


@pytest.mark.parametrize(
    "version_str,expected",
    [
        ("0.5.0", "0.5.0"),
        ("1.0.0", "1.0.0"),
        ("1.2.0", "1.2.0"),
        ("1.2.8", "1.2.8"),
        ("2.0.0rc1", "2.0.0-rc1"),
        ("2.0.0-rc1", "2.0.0-rc1"),
        ("2020.6.19", "2020.6.19"),
        ("2.0.0.dev1234", "2.0.0-dev1234"),
        ("2.0.0.dev", "2.0.0-dev"),
    ],
)
@mock.patch("ddtrace.internal.utils.version.get_version")
def test_version_agent_format(mock_get_version, version_str, expected):
    # type: (mock.Mock, str, typing.Tuple[int, int, int]) -> None
    mock_get_version.return_value = version_str
    version_agent_format = _get_version_agent_format()
    assert version_agent_format == expected
    _assert_and_get_version_agent_format(version_agent_format)

import typing

import pytest

from ddtrace.internal.utils.version import parse_version


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

import datetime
import sys

import pytest

from ddtrace.internal.utils.time import parse_isoformat


@pytest.mark.parametrize(
    "date_str,expected",
    [
        ("2022-09-01T01:00:00+02:00", datetime.datetime(2022, 8, 31, 23, 0)),
        ("2022-07-31T22:00:00Z", datetime.datetime(2022, 7, 31, 22, 0)),
        ("2022-07-31T22:00:00", None),
        ("2022-07-31", None),
        ("2022-07-3122:00:00", None),
        ("", None),
        ("aa", None),
    ],
)
@pytest.mark.skipif(sys.version_info > (3, 0, 0), reason="Python 2 tests")
def test_parse_isoformat_py2(date_str, expected):
    # type: (str, datetime.datetime) -> None
    result = parse_isoformat(date_str)
    assert result == expected


@pytest.mark.parametrize(
    "date_str,expected",
    [
        ("2022-07-31T22:00:00Z", datetime.datetime(2022, 7, 31, 22, 0)),
        ("2022-07-31T22:00:00", None),
        ("2022-07-31", None),
        ("2022-07-3122:00:00", None),
        ("", None),
        ("aa", None),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 5, 0) or sys.version_info >= (3, 7, 0), reason="Python 3.5 and 3.6 tests")
def test_parse_isoformat_py36(date_str, expected):
    # type: (str, datetime.datetime) -> None
    result = parse_isoformat(date_str)
    assert result == expected


@pytest.mark.skipif(sys.version_info < (3, 5, 0) or sys.version_info >= (3, 7, 0), reason="Python 3.5 and 3.6 tests")
def test_parse_isoformat_py36_with_timezone():
    # type: () -> None
    result = parse_isoformat("2022-09-01T01:00:00+02:00")
    assert result == datetime.datetime(2022, 8, 31, 23, 0)


@pytest.mark.parametrize(
    "date_str,expected",
    [
        ("2022-07-31T22:00:00Z", datetime.datetime(2022, 7, 31, 22, 0)),
        ("2022-07-31T22:00:00", datetime.datetime(2022, 7, 31, 22, 0)),
        ("2022-07-31", datetime.datetime(2022, 7, 31, 0, 0)),
        ("2022-07-3122:00:00", None),
        ("", None),
        ("aa", None),
    ],
)
@pytest.mark.skipif(sys.version_info < (3, 7, 0), reason="Python 3 tests")
def test_parse_isoformat_py3(date_str, expected):
    # type: (str, datetime.datetime) -> None
    result = parse_isoformat(date_str)
    assert result == expected


@pytest.mark.skipif(sys.version_info < (3, 7, 0), reason="Python 3 tests")
def test_parse_isoformat_py3_with_timezone():
    # type: () -> None
    result = parse_isoformat("2022-09-01T01:00:00+02:00")
    assert result == datetime.datetime(2022, 9, 1, 1, 0, tzinfo=datetime.timezone(datetime.timedelta(seconds=7200)))

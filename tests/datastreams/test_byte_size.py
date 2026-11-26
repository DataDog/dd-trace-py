import pytest

from ddtrace.internal.datastreams.utils import _calculate_byte_size


class _Text(str):
    pass


class _Data(bytes):
    pass


@pytest.mark.parametrize(
    "data,expected",
    [
        ("", 0),
        ("ascii", 5),
        ("välue", 6),
        ("你好", 6),
        ("\ud800x", 2),
        (_Text("välue"), 6),
        (b"", 0),
        (b"\xff\x00", 2),
        (_Data(b"abc"), 3),
        (bytearray(b"abc"), 0),
        ({"k": "välue", "n": {"x": b"yz"}}, 11),
        (None, 0),
        (42, 0),
    ],
)
def test_calculate_byte_size(data, expected):
    assert _calculate_byte_size(data) == expected

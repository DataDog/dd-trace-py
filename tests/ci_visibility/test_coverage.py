import pytest

from ddtrace.internal.ci_visibility.coverage import segments


@pytest.mark.parametrize(
    "lines,expected_segments",
    [
        ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [(1, 0, 10, 0, -1)]),
        ([1, 2, 4, 5, 6, 7, 8, 9, 10], [(1, 0, 2, 0, -1), (4, 0, 10, 0, -1)]),
        ([1, 3, 4, 5, 6, 7, 8, 9, 10], [(1, 0, 1, 0, -1), (3, 0, 10, 0, -1)]),
        ([1, 2, 3, 4, 5, 6, 7, 8, 10], [(1, 0, 8, 0, -1), (10, 0, 10, 0, -1)]),
        ([1, 2, 3, 4, 10, 5, 6, 7, 8], [(1, 0, 8, 0, -1), (10, 0, 10, 0, -1)]),
    ],
)
def test_segments(lines, expected_segments):
    assert segments(lines) == expected_segments

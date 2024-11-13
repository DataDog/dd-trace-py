from typing import NamedTuple

import pytest

from ddtrace._trace.telemetry import _span_pointer_count_to_tag


def test_span_pointer_count_to_tag_returns_strings() -> None:
    for count in range(0, 500):
        tag = _span_pointer_count_to_tag(count)

        assert isinstance(tag, str)
        assert tag != ""


class SpanPointerTagCase(NamedTuple):
    count: int
    expected: str


@pytest.mark.parametrize(
    "test_case",
    [
        SpanPointerTagCase(
            count=0,
            expected="0",
        ),
        SpanPointerTagCase(
            count=1,
            expected="1",
        ),
        SpanPointerTagCase(
            count=5,
            expected="5",
        ),
        SpanPointerTagCase(
            count=15,
            expected="15",
        ),
        SpanPointerTagCase(
            count=25,
            expected="21-50",
        ),
        SpanPointerTagCase(
            count=95,
            expected="51-100",
        ),
        SpanPointerTagCase(
            count=1000,
            expected="101+",
        ),
    ],
    ids=lambda test_case: f"count={test_case.count}",
)
def test_span_pointer_count_to_tag(test_case: SpanPointerTagCase) -> None:
    assert _span_pointer_count_to_tag(test_case.count) == test_case.expected

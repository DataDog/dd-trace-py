from typing import NamedTuple

import pytest

from ddtrace._trace.telemetry import _span_pointer_count_to_tag


def test_span_pointer_count_to_tag_returns_strings() -> None:
    unique_tags = set()

    for count in range(-10, 500):
        tag = _span_pointer_count_to_tag(count)

        assert isinstance(tag, str)
        assert tag != ""

        unique_tags.add(tag)

    reasonable_cadinality_limit = 15
    assert len(unique_tags) <= reasonable_cadinality_limit


class SpanPointerTagCase(NamedTuple):
    count: int
    expected: str


@pytest.mark.parametrize(
    "test_case",
    [
        SpanPointerTagCase(
            count=-1,
            expected="negative",
        ),
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
            expected="11-20",
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

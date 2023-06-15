#!/usr/bin/env python3
from ast import literal_eval

import pytest

from ddtrace import Tracer
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.coverage import cover
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


def test_cover():
    tracer = Tracer()
    span = tracer.start_span("cover_span")

    with cover(span):
        pytest.main(["tests/utils.py"])

    res = literal_eval(span.get_tag(COVERAGE_TAG_NAME))

    assert "files" in res
    assert len(res["files"]) == 3

    for x in res["files"]:
        if x["filename"].endswith("ddtrace/internal/module.py"):
            assert x["segments"] == [
                [100, 0, 100, 0, -1],
                [102, 0, 102, 0, -1],
                [112, 0, 113, 0, -1],
                [115, 0, 115, 0, -1],
                [158, 0, 159, 0, -1],
                [164, 0, 169, 0, -1],
                [172, 0, 172, 0, -1],
                [176, 0, 176, 0, -1],
                [178, 0, 178, 0, -1],
                [186, 0, 186, 0, -1],
                [197, 0, 197, 0, -1],
                [201, 0, 201, 0, -1],
                [203, 0, 206, 0, -1],
                [214, 0, 214, 0, -1],
                [217, 0, 217, 0, -1],
                [220, 0, 220, 0, -1],
                [222, 0, 223, 0, -1],
                [250, 0, 250, 0, -1],
                [254, 0, 254, 0, -1],
                [259, 0, 259, 0, -1],
                [269, 0, 269, 0, -1],
                [292, 0, 293, 0, -1],
                [296, 0, 297, 0, -1],
                [299, 0, 299, 0, -1],
                [302, 0, 302, 0, -1],
                [344, 0, 344, 0, -1],
                [356, 0, 359, 0, -1],
                [363, 0, 363, 0, -1],
                [371, 0, 371, 0, -1],
                [405, 0, 406, 0, -1],
                [408, 0, 408, 0, -1],
                [410, 0, 411, 0, -1],
                [413, 0, 413, 0, -1],
                [417, 0, 417, 0, -1],
                [420, 0, 420, 0, -1],
                [422, 0, 424, 0, -1],
                [426, 0, 426, 0, -1],
                [428, 0, 428, 0, -1],
                [431, 0, 431, 0, -1],
            ]
        elif x["filename"].endswith("ddtrace/contrib/pytest/plugin.py"):
            assert x["segments"] == [
                [201, 0, 201, 0, -1],
                [203, 0, 208, 0, -1],
                [211, 0, 216, 0, -1],
                [219, 0, 220, 0, -1],
                [247, 0, 247, 0, -1],
            ]
        elif x["filename"].endswith("ddtrace/internal/ci_visibility/coverage.py"):
            assert x["segments"] == [[62, 0, 62, 0, -1]]

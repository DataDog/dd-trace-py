#!/usr/bin/env python3
from ast import literal_eval
from os import getcwd

import pytest

from ddtrace import Tracer
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.coverage import Coverage
from ddtrace.internal.ci_visibility.coverage import _coverage_end
from ddtrace.internal.ci_visibility.coverage import _coverage_start
from ddtrace.internal.ci_visibility.coverage import _initialize
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


@pytest.mark.skipif(Coverage is None, reason="Coverage not available")
def test_cover():
    tracer = Tracer()
    span = tracer.start_span("cover_span")

    _initialize(getcwd())
    _coverage_start()
    pytest.main(["tests/utils.py"])
    _coverage_end(span)

    res = literal_eval(span.get_tag(COVERAGE_TAG_NAME))

    assert "files" in res
    assert len(res["files"]) == 3

    for filename in [x["filename"] for x in res["files"]]:
        assert filename.endswith(
            (
                "ddtrace/internal/module.py",
                "ddtrace/contrib/pytest/plugin.py",
                "ddtrace/internal/ci_visibility/coverage.py",
            )
        )

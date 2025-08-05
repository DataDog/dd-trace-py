import json

from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.coverage.util import collapse_ranges
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


def assert_stats(rec, **outcomes):
    """
    Assert that the correct number of test results of each type is present in a test run.

    This is similar to `rec.assertoutcome()`, but works with test statuses other than 'passed', 'failed' and 'skipped'.
    """
    stats = {**rec.getcall("pytest_terminal_summary").terminalreporter.stats}
    stats.pop("", None)

    for outcome, expected_count in outcomes.items():
        actual_count = len(stats.pop(outcome, []))
        assert actual_count == expected_count, f"Expected {expected_count} {outcome} tests, got {actual_count}"

    assert not stats, "Found unexpected stats in test results: {', '.join(stats.keys())}"


def _get_tuples_from_bytearray(bitmap):
    coverage_lines = CoverageLines()
    coverage_lines._lines = bitmap
    return collapse_ranges(coverage_lines.to_sorted_list())


def _get_tuples_from_segments(segments):
    return list((segment[0], segment[2]) for segment in segments)


def _get_span_coverage_data(span, use_plugin_v2=False):
    """Returns an abstracted view of the coverage data from the span that is independent of the coverage format."""
    if use_plugin_v2:
        tag_data = span.get_struct_tag(COVERAGE_TAG_NAME)
        assert tag_data is not None, f"Coverage data not found in span {span}"
        return {
            file_data["filename"]: _get_tuples_from_bytearray(file_data["bitmap"]) for file_data in tag_data["files"]
        }

    else:
        # This will raise an exception and the test will fail if the tag is not found
        tag_data = json.loads(span.get_tag(COVERAGE_TAG_NAME))
        return {
            file_data["filename"]: _get_tuples_from_segments(file_data["segments"]) for file_data in tag_data["files"]
        }

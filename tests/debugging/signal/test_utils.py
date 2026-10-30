"""Tests for IncompleteCapture, threaded through capture_value()/
capture_pairs() to record the first capture.incomplete reason at the exact
point each guardrail limit is decided.
"""

from ddtrace.debugging._signal.utils import IncompleteCapture
from ddtrace.debugging._signal.utils import capture_pairs
from ddtrace.debugging._signal.utils import capture_value


class _Nested:
    def __init__(self):
        self.child = object()


class _TwoFields:
    def __init__(self):
        self.a = 1
        self.b = 2


def test_incomplete_capture_none_when_complete():
    incomplete = IncompleteCapture()
    capture_value(1, incomplete=incomplete)
    assert incomplete.reason is None


def test_incomplete_capture_depth():
    incomplete = IncompleteCapture()
    capture_value(_Nested(), level=0, incomplete=incomplete)
    assert incomplete.reason == "depth"


def test_incomplete_capture_field_count():
    incomplete = IncompleteCapture()
    capture_value(_TwoFields(), maxfields=1, incomplete=incomplete)
    assert incomplete.reason == "fieldCount"


def test_incomplete_capture_collection_size():
    incomplete = IncompleteCapture()
    capture_value(list(range(100)), maxsize=1, incomplete=incomplete)
    assert incomplete.reason == "collectionSize"


def test_incomplete_capture_collection_size_for_mapping():
    incomplete = IncompleteCapture()
    capture_value({i: i for i in range(100)}, maxsize=1, incomplete=incomplete)
    assert incomplete.reason == "collectionSize"


def test_incomplete_capture_string_length():
    incomplete = IncompleteCapture()
    capture_value("a" * 1000, maxlen=10, incomplete=incomplete)
    assert incomplete.reason == "stringLength"


def test_incomplete_capture_timeout_stopping_cond():
    def timeout(_):
        return True

    incomplete = IncompleteCapture()
    capture_value([1, 2, 3], stopping_cond=timeout, incomplete=incomplete)
    assert incomplete.reason == "timeout"


def test_incomplete_capture_unrecognized_stopping_cond_maps_to_other():
    def some_other_budget(_):
        return True

    incomplete = IncompleteCapture()
    capture_value([1, 2, 3], stopping_cond=some_other_budget, incomplete=incomplete)
    assert incomplete.reason == "other"


def test_incomplete_capture_ignores_redaction():
    """Redaction is a privacy decision, not a guardrail limit. Redacted
    fields are built by redacted_value() directly, bypassing capture_value()
    entirely, so they never reach the tracker in the first place.
    """
    incomplete = IncompleteCapture()
    capture_pairs([("password", "s3cr3t")], incomplete=incomplete)
    assert incomplete.reason is None


def test_incomplete_capture_nested_nothing_incomplete_leaves_none():
    class _Outer:
        def __init__(self):
            self.inner = _TwoFields()

    incomplete = IncompleteCapture()
    capture_value(_Outer(), incomplete=incomplete)
    assert incomplete.reason is None


def test_incomplete_capture_nested_limit_propagates_up():
    class _Outer:
        def __init__(self):
            self.values = list(range(100))

    incomplete = IncompleteCapture()
    capture_value(_Outer(), maxsize=1, incomplete=incomplete)
    assert incomplete.reason == "collectionSize"


def test_incomplete_capture_via_capture_pairs_first_match_wins():
    incomplete = IncompleteCapture()
    capture_pairs(
        [("big_list", list(range(100))), ("big_dict", {i: i for i in range(100)})],
        maxsize=1,
        incomplete=incomplete,
    )
    assert incomplete.reason == "collectionSize"


def test_incomplete_capture_record_is_first_wins():
    incomplete = IncompleteCapture()
    incomplete.record("fieldCount")
    incomplete.record("collectionSize")
    assert incomplete.reason == "fieldCount"


def test_incomplete_capture_record_string_truncated_does_not_override():
    incomplete = IncompleteCapture()
    incomplete.record("depth")
    incomplete.record_string_truncated()
    assert incomplete.reason == "depth"


def test_incomplete_capture_not_passed_is_a_no_op():
    """incomplete is optional -- omitting it must not change capture_value()'s
    own return value or raise.
    """
    assert capture_value(list(range(100)), maxsize=1) == {
        "type": "list",
        "elements": [{"type": "int", "value": "0"}],
        "size": 100,
        "notCapturedReason": "collectionSize",
    }

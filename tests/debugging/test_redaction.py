import time
from unittest import mock

import pytest

from ddtrace.debugging._expressions import DDExpressionEvaluationError
from ddtrace.debugging._expressions import EvaluationTimeoutError
from ddtrace.debugging._expressions import get_eval_deadline
from ddtrace.debugging._expressions import iterates
from ddtrace.debugging._expressions import set_eval_deadline
from ddtrace.debugging._redaction import DDRedactedExpression
from ddtrace.debugging._redaction import DDRedactedExpressionError
from ddtrace.debugging._redaction import DDTimedRedactedExpression
from ddtrace.debugging._redaction import dd_compile_redacted
from ddtrace.internal.settings.dynamic_instrumentation import config
from tests.debugging.utils import SLOW_SCOPE
from tests.debugging.utils import slow_timed_expr


class Obj:
    def __init__(self):
        self.password = "s3cr3t"
        self.name = "alice"
        self.data = {"token": "abc", "count": 42}


def test_getmember_redacted_attribute_raises():
    """__getmember__ raises DDRedactedExpressionError for sensitive attribute names."""
    expr = dd_compile_redacted({"getmember": [{"ref": "obj"}, "password"]})
    with pytest.raises(DDRedactedExpressionError, match="password"):
        expr({"obj": Obj()})


def test_getmember_safe_attribute_allowed():
    """__getmember__ passes through non-sensitive attribute names."""
    expr = dd_compile_redacted({"getmember": [{"ref": "obj"}, "name"]})
    assert expr({"obj": Obj()}) == "alice"


def test_index_redacted_string_key_raises():
    """__index__ raises DDRedactedExpressionError for sensitive string dict keys."""
    expr = dd_compile_redacted({"index": [{"ref": "d"}, "token"]})
    with pytest.raises(DDRedactedExpressionError, match="token"):
        expr({"d": {"token": "abc", "count": 42}})


def test_index_safe_string_key_allowed():
    """__index__ passes through non-sensitive string keys."""
    expr = dd_compile_redacted({"index": [{"ref": "d"}, "count"]})
    assert expr({"d": {"token": "abc", "count": 42}}) == 42


def test_index_integer_key_allowed():
    """__index__ passes through non-string keys without a redaction check."""
    expr = dd_compile_redacted({"index": [{"ref": "arr"}, 1]})
    assert expr({"arr": ["a", "b", "c"]}) == "b"


def test_on_compiler_error_non_redaction_falls_back_to_invalid():
    """on_compiler_error delegates to super() for non-redaction errors,
    producing _invalid_expression (returns None) rather than re-raising.
    """
    result = DDRedactedExpression.compile({"json": {"unknown_op": []}, "dsl": "bad"})
    assert result({"x": 1}) is None


def test_on_compiler_error_redaction_wraps_and_re_raises():
    """on_compiler_error stores the DDRedactedExpressionError so that eval
    re-raises it wrapped in DDExpressionEvaluationError.
    """
    result = DDRedactedExpression.compile({"json": {"ref": "password"}, "dsl": "password"})
    with pytest.raises(DDExpressionEvaluationError) as exc_info:
        result({"password": "s3cr3t"})
    assert isinstance(exc_info.value.__cause__, DDRedactedExpressionError)


# ---------------------------------------------------------------------------
# DDTimedRedactedExpression: cooperative deadline, normalized to a bare
# EvaluationTimeoutError
# ---------------------------------------------------------------------------


def test_timed_redacted_expression_raises_bare_timeout():
    expr = slow_timed_expr()

    with mock.patch.object(config, "evaluation_timeout_ms", 20):
        start = time.monotonic()
        with pytest.raises(EvaluationTimeoutError):
            expr.eval(SLOW_SCOPE)
        elapsed = time.monotonic() - start

    assert elapsed < 1.0
    # The deadline does not outlive the evaluation.
    assert get_eval_deadline() is None


@pytest.mark.parametrize(
    "ast",
    [
        {"all": [{"ref": "big"}, {"ne": [{"ref": "@it"}, -1]}]},
        {"filter": [{"ref": "big"}, {"ne": [{"ref": "@it"}, -1]}]},
        {"any": [{"ref": "big"}, {"any": [{"ref": "small"}, {"eq": [{"ref": "@it"}, -1]}]}]},
        {"len": {"filter": [{"ref": "big_dict"}, {"ne": [{"ref": "@value"}, -1]}]}},
    ],
    ids=["all", "filter", "nested-any", "filter-dict"],
)
def test_timed_redacted_expression_bounds_every_iteration(ast):
    expr = DDTimedRedactedExpression.compile({"dsl": "slow", "json": ast})
    scope = dict(SLOW_SCOPE, small=[1, 2, 3], big_dict=dict.fromkeys(range(2_000_000), 0))

    with mock.patch.object(config, "evaluation_timeout_ms", 20):
        with pytest.raises(EvaluationTimeoutError):
            expr.eval(scope)


class _SlowProperty:
    call_count = 0

    @property
    def value(self):
        _SlowProperty.call_count += 1
        end = time.perf_counter() + 0.005
        while time.perf_counter() < end:
            pass
        return 0


def test_timed_redacted_expression_slow_predicate_overshoots_by_one_call():
    """Chunks shrink to one element when the predicate is slow, so the deadline
    is overshot by about one predicate evaluation rather than a whole chunk.

    The primary assertion is on the number of predicate calls, not wall-clock
    elapsed time: elapsed time is also at the mercy of OS scheduling on a
    loaded host, independent of whether chunking itself is working -- the
    call count isolates the chunk-shrinking behavior this test is actually
    about from that noise. The wall-clock check is kept too, but only as a
    loose sanity bound (a fixed 64-element chunk would cost ~320ms; this
    gives several times that much headroom for scheduling jitter).
    """
    expr = DDTimedRedactedExpression.compile(
        {
            "dsl": "slow",
            "json": {"any": [{"ref": "xs"}, {"eq": [{"getmember": [{"ref": "@it"}, "value"]}, -1]}]},
        }
    )

    _SlowProperty.call_count = 0
    with mock.patch.object(config, "evaluation_timeout_ms", 20):
        start = time.monotonic()
        with pytest.raises(EvaluationTimeoutError):
            expr.eval({"xs": [_SlowProperty() for _ in range(1000)]})
        elapsed = time.monotonic() - start

    # One 4-element chunk (before the first shrink) plus a handful of
    # 1-element chunks while still under budget -- nowhere near the 64-cap.
    assert _SlowProperty.call_count < 20, (
        f"Expected chunks to shrink to ~1 element, but {_SlowProperty.call_count} calls were made"
    )
    assert elapsed < 1.5


def test_timed_redacted_expression_cost_rising_mid_collection():
    """When cheap elements are followed by slow ones, the chunk being consumed
    when the cost rises is bounded by the maximum chunk size.
    """
    expr = DDTimedRedactedExpression.compile(
        {
            "dsl": "slow",
            "json": {"any": [{"ref": "xs"}, {"eq": [{"getmember": [{"ref": "@it"}, "value"]}, -1]}]},
        }
    )

    class _Cheap:
        value = 0

    xs = [_Cheap() for _ in range(5000)] + [_SlowProperty() for _ in range(1000)]

    with mock.patch.object(config, "evaluation_timeout_ms", 20):
        start = time.monotonic()
        with pytest.raises(EvaluationTimeoutError):
            expr.eval({"xs": xs})
        elapsed = time.monotonic() - start

    # At most one 64-element chunk of 5ms calls past the budget (~340ms in
    # total); a 1024-element cap could reach several seconds.
    assert elapsed < 1.0


def test_timed_redacted_expression_nested_keeps_outer_deadline():
    """A nested evaluation (e.g. a probe hit from code an expression calls
    into) cannot extend the outer deadline, and restores it on exit.
    """
    outer_deadline = time.perf_counter_ns() + 1_000_000  # 1ms from now
    set_eval_deadline(outer_deadline)
    try:
        with mock.patch.object(config, "evaluation_timeout_ms", 5000):
            with pytest.raises(EvaluationTimeoutError):
                slow_timed_expr().eval(SLOW_SCOPE)
        assert get_eval_deadline() == outer_deadline
    finally:
        set_eval_deadline(None)


def test_timed_redacted_expression_disabled_when_timeout_not_positive():
    expr = DDTimedRedactedExpression.compile(
        {"dsl": "small", "json": {"any": [{"ref": "small"}, {"eq": [{"ref": "@it"}, -1]}]}}
    )

    with mock.patch.object(config, "evaluation_timeout_ms", -1):
        assert expr.eval({"small": range(100_000)}) is False
        assert get_eval_deadline() is None


class _CountingIterable:
    """Re-iterable collection whose iterator records every element it
    produces, like a lazily-fetching query or client.
    """

    def __init__(self, n=None):
        self.n = n
        self.produced = 0

    def __iter__(self):
        i = 0
        while self.n is None or i < self.n:
            self.produced += 1
            yield i
            i += 1


def test_timed_redacted_expression_pulls_no_extra_elements():
    """Timing must not change what the expression observes: a short-circuiting
    any() pulls exactly as many elements as it would without a deadline.
    """
    ast = {"any": [{"ref": "xs"}, {"eq": [{"ref": "@it"}, 2]}]}
    untimed = DDRedactedExpression.compile({"dsl": "any", "json": ast})
    timed = DDTimedRedactedExpression.compile({"dsl": "any", "json": ast})

    with mock.patch.object(config, "evaluation_timeout_ms", 1000):
        for expr in (untimed, timed):
            xs = _CountingIterable(1000)
            assert expr.eval({"xs": xs}) is True
            assert xs.produced == 3


def test_timed_redacted_expression_bounds_custom_iterable():
    # A never-ending custom iterable takes the element-by-element path
    expr = DDTimedRedactedExpression.compile(
        {"dsl": "slow", "json": {"any": [{"ref": "xs"}, {"eq": [{"ref": "@it"}, -1]}]}}
    )

    with mock.patch.object(config, "evaluation_timeout_ms", 20):
        with pytest.raises(EvaluationTimeoutError):
            expr.eval({"xs": _CountingIterable()})


@pytest.mark.parametrize(
    "ast,expected",
    [
        ({"eq": [{"ref": "x"}, 1]}, False),
        # operator names as literals or member names are not operators
        ({"eq": [{"ref": "any"}, "filter"]}, False),
        ({"getmember": [{"ref": "x"}, "all"]}, False),
        ({"any": [{"ref": "xs"}, True]}, True),
        ({"and": [True, {"not": {"isEmpty": {"filter": [{"ref": "xs"}, True]}}}]}, True),
        ({"len": {"getmember": [{"all": [{"ref": "xs"}, True]}, "x"]}}, True),
    ],
)
def test_iterates(ast, expected):
    assert iterates(ast) is expected


def test_timed_redacted_expression_skips_deadline_without_iteration():
    expr = DDTimedRedactedExpression.compile({"dsl": "x == 1", "json": {"eq": [{"ref": "x"}, 1]}})
    assert expr.iterates is False

    with (
        mock.patch.object(config, "evaluation_timeout_ms", 20),
        mock.patch("ddtrace.debugging._redaction.set_eval_deadline") as set_deadline,
    ):
        assert expr.eval({"x": 1}) is True
    set_deadline.assert_not_called()


def test_timed_redacted_expression_still_redacts():
    """DDTimedRedactedExpression still enforces attribute redaction --
    adding timing doesn't bypass DDRedactedExpression's own protections.
    """
    expr = DDTimedRedactedExpression.compile({"json": {"getmember": [{"ref": "obj"}, "password"]}, "dsl": "bad"})
    with mock.patch.object(config, "evaluation_timeout_ms", 50):
        with pytest.raises(DDExpressionEvaluationError) as exc_info:
            expr.eval({"obj": Obj()})
    assert isinstance(exc_info.value.__cause__, DDRedactedExpressionError)


def test_timed_redacted_expression_fast_callable_not_interrupted():
    """A callable finishing well under budget is never interrupted."""
    expr = DDTimedRedactedExpression(dsl="True", callable=lambda scope: True)

    with mock.patch.object(config, "evaluation_timeout_ms", 5000):
        assert expr.eval({}) is True

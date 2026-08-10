import pytest

from ddtrace.debugging._expressions import DDExpressionEvaluationError
from ddtrace.debugging._redaction import DDRedactedExpression
from ddtrace.debugging._redaction import DDRedactedExpressionError
from ddtrace.debugging._redaction import dd_compile_redacted


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

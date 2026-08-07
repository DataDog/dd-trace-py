"""except* exception groups in a wrapped body — Python 3.11+.

Collected only on 3.11+ (see conftest.py). except* introduces new opcodes and
exception-table regions the wrapping trampolines must preserve. (Note: return /
break / continue are illegal inside an except* block, so we assign and return after.)
"""

import pytest


def test_except_star_in_function_body(mech):
    def f(x):
        result = None
        try:
            raise ExceptionGroup("eg", [ValueError(x)])
        except* ValueError as eg:
            result = ("caught", len(eg.exceptions))
        return result

    assert mech.wrap_function(f)(7) == ("caught", 1)


def test_except_star_reraises_unmatched(mech):
    def f():
        try:
            raise ExceptionGroup("eg", [KeyError("k")])
        except* ValueError:
            pass  # KeyError is unmatched and propagates as a group

    g = mech.wrap_function(f)
    with pytest.raises(BaseExceptionGroup) as exc_info:  # noqa: F821 - builtin on 3.11+
        g()
    assert len(exc_info.value.exceptions) == 1

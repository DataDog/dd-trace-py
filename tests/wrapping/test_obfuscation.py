"""WrappingContext must refuse obfuscated code before wrap-side registration.

Uses a stub is_obfuscated_code rather than real PyArmor, matching
tests/debugging/function/test_store.py. This is mechanism-specific: only
WrappingContext.wrap raises ObfuscatedCodeError (internal.wrap logs and
returns the original function).
"""

from types import CodeType
from types import FunctionType
from typing import cast
from unittest import mock

import pytest

from ddtrace.internal.compat import is_at_least_py
from ddtrace.internal.utils.obfuscation import ObfuscatedCodeError
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.internal.wrapping.context import _UniversalWrappingContext


pytestmark = pytest.mark.mechanism_specific


class _NoopWrappingContext(WrappingContext):
    pass


def test_wrapping_context_raises_on_obfuscated_code_and_leaves_function_untouched() -> None:
    def f() -> int:
        return 1

    fn: FunctionType = cast(FunctionType, f)
    original_code: CodeType = fn.__code__

    with mock.patch("ddtrace.internal.wrapping.context.is_obfuscated_code", return_value=True):
        with pytest.raises(ObfuscatedCodeError):
            _NoopWrappingContext(fn).wrap()

    assert fn.__code__ is original_code, "obfuscated code was rewritten despite the failed wrap"
    assert not _UniversalWrappingContext.is_wrapped(fn)
    assert fn() == 1


@pytest.mark.skipif(not is_at_least_py(3, 15), reason="the code registry only exists on the sys.monitoring path")
def test_wrapping_context_rejects_obfuscated_code_before_taking_registry_lock() -> None:
    # Registry handling can unregister monitoring for a stale entry, which must
    # not happen for obfuscated code, so detection has to come first.
    def f() -> int:
        return 1

    ctx: _NoopWrappingContext = _NoopWrappingContext(cast(FunctionType, f))
    lock: mock.MagicMock = mock.MagicMock()
    lock.__enter__.side_effect = AssertionError("registry lock taken for obfuscated code")

    with mock.patch("ddtrace.internal.wrapping.context.is_obfuscated_code", return_value=True):
        with mock.patch("ddtrace.internal.wrapping.context._ctx_registry_lock", lock):
            with pytest.raises(ObfuscatedCodeError):
                ctx.wrap()

    lock.__enter__.assert_not_called()

import dis
import sys

import pytest

from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation


skipif_bytecode_injection_not_supported = pytest.mark.skipif(
    sys.version_info[:2] < (3, 11),
    reason="Injection is only supported for 3.11+",
)


@skipif_bytecode_injection_not_supported
def test_linetable_unchanged_when_no_injection():
    original = sample_function_1.__code__
    ic = InjectionContext(original, _sample_callback, lambda _: [])
    injected, _ = inject_invocation(ic, "some/path.py", "some.package")

    assert list(original.co_lines()) == list(injected.co_lines())
    assert dict(dis.findlinestarts(original)) == dict(dis.findlinestarts(injected))


@skipif_bytecode_injection_not_supported
def test_injection_works():
    accumulate = []

    def will_be_injected():
        accumulate.append(1)
        # in this spot we are going to inject accumulate(2)
        accumulate.append(3)

    def accumulate_2(*args):
        accumulate.append(2)

    original = will_be_injected.__code__
    # From dis.dis(will_be_injected), 46 is the opcode index of `accumulate.append(3)`
    ic = InjectionContext(original, accumulate_2, lambda _: [46])
    injected, _ = inject_invocation(ic, "some/path.py", "some.package")
    will_be_injected.__code__ = injected

    will_be_injected()

    assert accumulate == [1, 2, 3]


@skipif_bytecode_injection_not_supported
def test_injection_in_try_catch():
    accumulate = []

    def will_be_injected():
        accumulate.append(1)
        try:
            raise ValueError("this is a value error")
        except ValueError as _:
            # in this spot we are going to inject accumulate(2)
            print("I am handling the exception")
        accumulate.append(3)

    def accumulate_2(*args):
        accumulate.append(2)

    original = will_be_injected.__code__
    # From dis.dis(will_be_injected), 98 is the opcode index of `print('I am handling the exception')`
    ic = InjectionContext(original, accumulate_2, lambda _: [98])
    injected, _ = inject_invocation(ic, "some/path.py", "some.package")
    will_be_injected.__code__ = injected

    will_be_injected()

    assert accumulate == [1, 2, 3]


@skipif_bytecode_injection_not_supported
def test_linetable_adjustment():
    selected_line_starts = [e for e in list(dis.findlinestarts(sample_function_short_jumps.__code__))[1:-1]]
    injection_offsets = [o for o, l in selected_line_starts]

    original_code = sample_function_short_jumps.__code__
    ic = InjectionContext(original_code, nothing, lambda _: injection_offsets)
    injected_code, _ = inject_invocation(ic, "some/path.py", "some.package")

    selected_line_starts_post_injection = [e for e in list(dis.findlinestarts(injected_code))[1:-1]]

    assert len(selected_line_starts) == len(selected_line_starts_post_injection), "Same number of lines"

    OFFSET = 0
    LINE = 1
    for idx, (o, l) in enumerate(selected_line_starts):
        assert l == selected_line_starts_post_injection[idx][LINE], "Every line is the same"
        # offset of line points to the same instructions
        assert (
            original_code.co_code[o] == injected_code.co_code[selected_line_starts_post_injection[idx][OFFSET]]
        ), "The corresponding opcode is the same"
        # argument corresponding to first line opcode corresponds
        assert (
            original_code.co_code[o + 1] == injected_code.co_code[selected_line_starts_post_injection[idx][OFFSET] + 1]
        ), "The corresponding argument is the same"


@skipif_bytecode_injection_not_supported
def test_exceptiontable_adjustment():
    selected_line_starts = [e for e in list(dis.findlinestarts(sample_function_short_jumps.__code__))[1:-1]]
    injection_offsets = [o for o, l in selected_line_starts]

    original_code = sample_function_short_jumps.__code__
    original_co_code = original_code.co_code
    original_exceptions = dis._parse_exception_table(original_code)

    ic = InjectionContext(original_code, nothing, lambda _: injection_offsets)
    injected_code, _ = inject_invocation(ic, "some/path.py", "some.package")
    injected_co_code = injected_code.co_code
    injected_exceptions = dis._parse_exception_table(injected_code)

    assert len(original_exceptions) == len(injected_exceptions), "Same number of exceptions"
    exceptions_cnt = len(original_exceptions)

    partially_translated_starts = []

    for idx in range(exceptions_cnt):
        original_entry = original_exceptions[idx]
        injected_entry = injected_exceptions[idx]
        # Starts are tested differently, because they do not simply translate by the previously injected code.
        # They might only partially translate to include the callback invocation.
        # So we collect the starts that are not simply translated, and those indexes MUST be in the injection_offsets
        if original_co_code[original_entry.start] != injected_co_code[injected_entry.start]:
            partially_translated_starts.append(original_entry.start)

        assert original_co_code[original_entry.end -
                                2] == injected_co_code[injected_entry.end - 2], "End (exclusive) opcode is the same"
        assert original_co_code[original_entry.target] == injected_co_code[injected_entry.target], "Target opcode is the same"
        assert original_entry.depth == injected_entry.depth, "Depth is the same"
        assert original_entry.lasti == injected_entry.lasti, "lasti is the same"

    assert set(partially_translated_starts).issubset(set(injection_offsets)
                                                     ), "All partially translated starts are in the injection offsets"


@skipif_bytecode_injection_not_supported
def test_try_finally_is_executed_when_callback_fails():
    value = 0
    BEFORE_TRY = 1
    AFTER_CALLBACK_ERROR = BEFORE_TRY << 1
    FINALLY = AFTER_CALLBACK_ERROR << 1
    IN_CALLBACK = FINALLY << 1

    def _broken_callback(*args):
        nonlocal value
        value |= IN_CALLBACK
        raise ValueError("this is in the callback")

    def the_function():
        nonlocal value
        try:
            value |= BEFORE_TRY
            raise ValueError("this is a value error")
        except ValueError as _:
            # in this spot we are going to inject the callback
            value |= AFTER_CALLBACK_ERROR
        finally:
            pass
            value |= FINALLY

    # Injection index from dis.dis(<function to inject into>)
    ic = InjectionContext(the_function.__code__, _broken_callback, lambda _: [66])
    injected_code, _ = inject_invocation(ic, "some/path.py", "some.package")
    the_function.__code__ = injected_code

    try:
        the_function()
        pytest.fail("it should not be here")
    except ValueError as e:
        assert str(e) == "this is in the callback"

    assert value == BEFORE_TRY + FINALLY + IN_CALLBACK  # DEV: make clear in docs that AFTER_CALLBACK_ERROR won't be executed


@skipif_bytecode_injection_not_supported
def test_try_finally_is_executed_when_callback_succeed():
    value = 0
    BEFORE_TRY = 1
    AFTER_CALLBACK_ERROR = 2
    FINALLY = 4
    IN_CALLBACK = 8
    SECOND_FINALLY = 16

    def _callback(*args):
        nonlocal value
        value += IN_CALLBACK

    def the_function():
        nonlocal value
        try:
            value |= BEFORE_TRY
            raise ValueError("this is a value error")
        except ValueError as _:
            # in this spot we are going to inject the callback
            value |= AFTER_CALLBACK_ERROR
        finally:
            value |= FINALLY

        a = 1
        b = 2
        c = a + b

        try:
            value |= BEFORE_TRY
            raise ValueError("this is a value error")
        except ValueError as _:
            # in this spot we are going to inject the callback
            value |= AFTER_CALLBACK_ERROR
        finally:
            value |= SECOND_FINALLY

    # Injection index from dis.dis(<function to inject into>)
    ic = InjectionContext(the_function.__code__, _callback, lambda _: [66])
    injected_code, _ = inject_invocation(ic, "some/path.py", "some.package")
    the_function.__code__ = injected_code

    the_function()

    assert value == BEFORE_TRY + AFTER_CALLBACK_ERROR + FINALLY + IN_CALLBACK + SECOND_FINALLY


def sample_function_1():
    a = 1
    b = 2
    _ = a + b


def sample_function_short_jumps():
    a = 1
    b = 2
    if True:
        pass
    c = a + b

    try:
        raise ValueError("this is a value error")
    except ValueError as _:
        # in this spot we are going to inject accumulate(2)
        print("I am handling the exception")

    for i in range(3):
        print(i > 1)

    i = 10
    while i > 0:
        i -= 1

    try:
        raise ValueError("another value error")
    except ValueError as _:
        # in this spot we are going to inject accumulate(2)
        print("I am handling the exception differently")

    for i in range(3):
        print(i > 1)

    i = 10
    while i > 0:
        i -= 1

    return c


def nothing(*args):
    some_var = "do nothing"
    return some_var


def _sample_callback(*arg):
    print("callback")

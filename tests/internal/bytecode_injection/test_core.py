import dis
import sys

import pytest

from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation


skipif_bytecode_injection_not_supported = pytest.mark.skipif(
    sys.version_info[:2] < (3, 10),
    reason="Injection is only supported for 3.10+",
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

    # Injection lines retrieved from dis.dis(will_be_injected) at the opcode index of `accumulate.append(3)`
    if sys.version_info[:2] == (3, 10):
        injection_lines = [10]
    elif sys.version_info[:2] == (3, 11):
        injection_lines = [46]
    else:
        injection_lines = []

    ic = InjectionContext(original, accumulate_2, lambda _: injection_lines)
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

    # Injection lines retrieved from dis.dis(will_be_injected) at the opcode index of `accumulate.append(3)`
    if sys.version_info[:2] == (3, 10):
        injection_lines = [34]
    elif sys.version_info[:2] == (3, 11):
        injection_lines = [98]
    else:
        injection_lines = []

    ic = InjectionContext(original, accumulate_2, lambda _: injection_lines)
    injected, _ = inject_invocation(ic, "some/path.py", "some.package")
    will_be_injected.__code__ = injected

    will_be_injected()

    assert accumulate == [1, 2, 3]


@skipif_bytecode_injection_not_supported
def test_linetable_adjustment():
    selected_line_starts = [e for e in list(dis.findlinestarts(sample_function_short_jumps.__code__))[1:-1]]
    injection_offsets = [o for o, _ in selected_line_starts]

    original_code = sample_function_short_jumps.__code__
    ic = InjectionContext(original_code, nothing, lambda _: injection_offsets)
    injected_code, _ = inject_invocation(ic, "some/path.py", "some.package")

    selected_line_starts_post_injection = [e for e in list(dis.findlinestarts(injected_code))[1:-1]]

    assert len(selected_line_starts) == len(selected_line_starts_post_injection), "Same number of lines"

    OFFSET = 0
    LINE = 1

    for idx, (original_offset, original_line_start) in enumerate(selected_line_starts):
        assert original_line_start == selected_line_starts_post_injection[idx][LINE], "Every line is the same"

        # offset of line points to the same instructions
        assert (
            original_code.co_code[original_offset]
            == injected_code.co_code[selected_line_starts_post_injection[idx][OFFSET]]
        ), "The corresponding opcode is the same"

        if original_code.co_code[original_offset] in dis.hasjrel:
            # In case of a jump, we assert that the (dereferenced) target is the same
            # DEV: expand to reverse jumps
            original_arg = original_code.co_code[original_offset + 1]
            injected_arg = injected_code.co_code[selected_line_starts_post_injection[idx][OFFSET] + 1]

            # dereferencing the jump target (DEV: only depth 1, for now)
            assert (
                original_code.co_code[original_offset + (original_arg << 1)]
                == injected_code.co_code[selected_line_starts_post_injection[idx][OFFSET] + (injected_arg << 1)]
            ), "The corresponding target opcode is the same"
        else:
            assert (
                original_code.co_code[original_offset + 1]
                == injected_code.co_code[selected_line_starts_post_injection[idx][OFFSET] + 1]
            ), "The corresponding argument is the same"


@pytest.mark.skipif(
    sys.version_info[:2] < (3, 11),
    reason="Exception table was introduced in 3.11",
)
def test_exceptiontable_adjustment():
    selected_line_starts = [e for e in list(dis.findlinestarts(sample_function_short_jumps.__code__))[1:-1]]
    injection_offsets = [o for o, _ in selected_line_starts]

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

        assert (
            original_co_code[original_entry.end - 2] == injected_co_code[injected_entry.end - 2]
        ), "End (exclusive) opcode is the same"
        assert (
            original_co_code[original_entry.target] == injected_co_code[injected_entry.target]
        ), "Target opcode is the same"
        assert original_entry.depth == injected_entry.depth, "Depth is the same"
        assert original_entry.lasti == injected_entry.lasti, "lasti is the same"

    assert set(partially_translated_starts).issubset(
        set(injection_offsets)
    ), "All partially translated starts are in the injection offsets"


@skipif_bytecode_injection_not_supported
def test_try_finally_is_executed_when_callback_fails():
    value = 0
    BEFORE_TRY = 1
    AFTER_CALLBACK_ERROR = 2
    FINALLY = 4
    IN_CALLBACK = 8

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
            value |= FINALLY

    # Injection index from dis.dis(<function to inject into>)
    INJECTION_INDEXES = {
        (3, 10): [34],
        (3, 11): [66],
    }

    ic = InjectionContext(the_function.__code__, _broken_callback, lambda _: INJECTION_INDEXES[sys.version_info[:2]])
    injected_code, _ = inject_invocation(ic, "some/path.py", "some.package")
    the_function.__code__ = injected_code

    try:
        the_function()
        pytest.fail("it should not be here")
    except ValueError as e:
        assert str(e) == "this is in the callback"

    assert (
        value == BEFORE_TRY + FINALLY + IN_CALLBACK
    )  # DEV: make clear in docs that AFTER_CALLBACK_ERROR won't be executed


@skipif_bytecode_injection_not_supported
def test_try_finally_is_executed_when_callback_succeed():
    value = 0
    callback_invocations = 0

    BEFORE_TRY_1 = 1
    BEFORE_TRY_2 = BEFORE_TRY_1 << 1
    AFTER_CALLBACK_ERROR_1 = BEFORE_TRY_2 << 1
    AFTER_CALLBACK_ERROR_2 = AFTER_CALLBACK_ERROR_1 << 1
    FINALLY_1 = AFTER_CALLBACK_ERROR_2 << 1
    FINALLY_2 = FINALLY_1 << 1

    def _callback(*args):
        nonlocal callback_invocations
        callback_invocations += 1

    def the_function():
        nonlocal value
        try:
            value |= BEFORE_TRY_1
            raise ValueError("this is a value error")
        except ValueError as _:
            # in this spot we are going to inject the callback
            value |= AFTER_CALLBACK_ERROR_1
        finally:
            value |= FINALLY_1

        a = 1
        b = 2
        _ = a + b

        try:
            value |= BEFORE_TRY_2
            raise ValueError("this is a value error")
        except ValueError as _:
            # in this spot we are going to inject the callback
            value |= AFTER_CALLBACK_ERROR_2
        finally:
            value |= FINALLY_2

    dis.dis(the_function)
    # Injection index from dis.dis(<function to inject into>)
    INJECTION_INDEXES = {
        (3, 10): [34, 136],
        (3, 11): [66, 216],
    }

    ic = InjectionContext(the_function.__code__, _callback, lambda _: INJECTION_INDEXES[sys.version_info[:2]])
    injected_code, _ = inject_invocation(ic, "some/path.py", "some.package")
    the_function.__code__ = injected_code

    the_function()

    assert callback_invocations == 2

    assert (
        value == BEFORE_TRY_1 + AFTER_CALLBACK_ERROR_1 + FINALLY_1 + BEFORE_TRY_2 + AFTER_CALLBACK_ERROR_2 + FINALLY_2
    )


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

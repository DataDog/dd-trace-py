import dis
import pytest
import sys

from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation


def test_linetable_unchanged_when_no_injection():
    original = sample_function_1.__code__
    ic = InjectionContext(original, _sample_callback, lambda _: [])
    injected, _ = inject_invocation(ic, "some/path.py", "some.package")

    assert list(original.co_lines()) == list(injected.co_lines())
    assert dict(dis.findlinestarts(original)) == dict(dis.findlinestarts(injected))


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


@pytest.mark.skipif(
    sys.version_info[:2] < (3, 11),
    reason="Injection is only supported for 3.11+",
)
def test_linetable_compilation():
    selected_line_starts = [e for e in list(dis.findlinestarts(sample_function_short_jumps.__code__))[1:-1]]
    injection_offsets = [o for o, l in selected_line_starts]

    original_code = sample_function_short_jumps.__code__
    ic = InjectionContext(original_code, nothing, lambda _: injection_offsets)
    injected_code, _ = inject_invocation(ic, "some/path.py", "some.package")

    selected_line_starts_post_injection = [e for e in list(dis.findlinestarts(injected_code))[1:-1]]

    assert len(selected_line_starts) == len(selected_line_starts_post_injection)

    OFFSET = 0
    LINE = 1
    for idx, (o, l) in enumerate(selected_line_starts):
        assert l == selected_line_starts_post_injection[idx][LINE]
        # offset of line points to the same instructions
        assert original_code.co_code[o] == injected_code.co_code[selected_line_starts_post_injection[idx][OFFSET]]
        # argument corresponding to first line opcode corresponds
        assert original_code.co_code[o +
                                     1] == injected_code.co_code[selected_line_starts_post_injection[idx][OFFSET] + 1]


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

    return c


def nothing(*args):
    some_var = 'do nothing'
    return some_var


def _sample_callback(*arg):
    print("callback")

from contextlib import contextmanager
import sys

import mock
import pytest

from ddtrace.internal.bytecode_injection import InvalidLine
from ddtrace.internal.bytecode_injection import eject_hook
from ddtrace.internal.bytecode_injection import eject_hooks
from ddtrace.internal.bytecode_injection import inject_hook
from ddtrace.internal.bytecode_injection import inject_hooks
from ddtrace.internal.utils.inspection import linenos


@contextmanager
def injected_hook(f, hook, arg, line=None):
    code = f.__code__

    if line is None:
        line = min(linenos(f))

    try:
        inject_hook(f, hook, line, arg)
    except InvalidLine:
        import dis

        dis.dis(f)
        raise

    yield f

    eject_hook(f, hook, line, arg)

    assert f.__code__ is not code


def injection_target(a, b):
    a = a ^ b
    b = a ^ b
    # comment
    a = a ^ b

    return a, b


def loop_target(n):
    for _ in range(n):
        a, b = injection_target(n, n + 1)


def generator_target(n):
    yield "begin"
    # yield from range(n)
    yield "end"
    return


def multiline(new_target):
    int(
        "0x2a",
        base=16,
    )


def test_inject_hook():
    hook = mock.Mock()

    lo = min(linenos(injection_target))
    result = inject_hook(injection_target, hook, lo, 42)

    assert result(1, 2) == (2, 1)

    hook.assert_called_once_with(42)


def test_eject_hook():
    hook = mock.Mock()

    lo = min(linenos(injection_target))
    result = eject_hook(inject_hook(injection_target, hook, lo, hook), hook, lo, hook)

    assert result(1, 2) == (2, 1)

    hook.assert_not_called()


def test_inject_hooks():
    n = 2
    hooks = [mock.Mock("hook%d" % _) for _ in range(n)]

    lo = min(linenos(injection_target))
    lines = list(range(lo, lo + n))
    idents = list(range(42, 42 + n))

    failed = inject_hooks(injection_target, list(zip(hooks, lines, idents)))
    assert failed == []

    assert injection_target(1, 2) == (2, 1)

    for hook, ident in zip(hooks, idents):
        hook.assert_called_with(ident)


def test_inject_hooks_some_invalid():
    lines = list(linenos(injection_target))
    lines.append(max(lines) + 10)
    n = len(lines)
    hooks = [mock.Mock("hook%d" % _) for _ in range(n)]

    idents = list(range(42, 42 + n))

    failed = inject_hooks(injection_target, list(zip(hooks, lines, idents)))
    assert failed == [(hooks[-1], lines[-1], idents[-1])]

    assert injection_target(1, 2) == (2, 1)

    for hook, ident in zip(hooks[:-1], idents):
        hook.assert_called_with(ident)


def test_eject_hooks():
    hooks = [mock.Mock("hook%d" % _) for _ in range(2)]

    lo = min(linenos(injection_target))
    lines = list(range(lo, lo + 2))
    hook_data = list(zip(hooks, lines, hooks))
    failed = inject_hooks(injection_target, hook_data)
    assert failed == []

    failed = eject_hooks(injection_target, hook_data)
    assert failed == []

    assert injection_target(1, 2) == (2, 1)

    for hook in hooks:
        hook.assert_not_called()


def test_eject_hooks_some_invalid():
    hooks = [mock.Mock("hook%d" % _) for _ in range(2)]

    lo = min(linenos(injection_target))
    lines = list(range(lo, lo + 2))
    hook_data = list(zip(hooks, lines, hooks))
    failed = inject_hooks(injection_target, hook_data)
    assert failed == []

    invalid_hook = (hooks[-1], lines[-1] + 200, hooks[-1])
    hook_data.append(invalid_hook)
    failed = eject_hooks(injection_target, hook_data)
    assert failed == [invalid_hook]

    assert injection_target(1, 2) == (2, 1)

    for hook in hooks:
        hook.assert_not_called()


def test_eject_hooks_same_line():
    hooks = [mock.Mock("hook%d" % _) for _ in range(3)]

    lo = min(linenos(injection_target)) + 1
    lines = [lo] * 3

    failed = inject_hooks(injection_target, list(zip(hooks, lines, hooks)))
    assert failed == []

    failed = eject_hooks(injection_target, list(zip(hooks, lines, hooks)))
    assert failed == []

    assert injection_target(1, 2) == (2, 1)

    for hook in hooks:
        hook.assert_not_called()


def test_inject_out_of_bounds():
    with pytest.raises(InvalidLine):
        inject_hook(injection_target, lambda x: x, 0, 0)


def test_inject_invalid_line():
    with pytest.raises(InvalidLine):
        ls = linenos(injection_target)
        gaps = set(range(min(ls), max(ls) + 1)) - ls
        inject_hook(injection_target, lambda x: x, gaps.pop(), 0)


def test_inject_instance_method():
    from tests.submod.stuff import Stuff

    lo = min(linenos(Stuff.instancestuff))
    hook = mock.Mock()
    old_method = Stuff.instancestuff
    inject_hook(Stuff.instancestuff, hook, lo, 0)

    stuff = Stuff()
    assert stuff.instancestuff(42) == 42
    hook.assert_called_once_with(0)

    Stuff.instancestuff = old_method


def test_inject_in_loop():
    lo = min(linenos(loop_target)) + 1
    hook = mock.Mock()

    n = 10
    new_loop_target = inject_hook(loop_target, hook, lo, 42)
    new_loop_target(n)

    assert hook.call_count == n


@pytest.mark.skipif(sys.version_info > (3, 12), reason="Fails on 3.13")
def test_inject_in_generator():
    lo = next(iter(linenos(generator_target)))
    hook = mock.Mock()

    new_generator_target = inject_hook(generator_target, hook, lo, 42)
    list(new_generator_target(42))

    assert hook.call_count == 1


def test_inject_in_multiline():
    lo = min(linenos(multiline)) + 2
    hook = mock.Mock()

    new_target = inject_hook(multiline, hook, lo, 42)
    new_target(new_target)

    assert hook.call_count == 1


def test_property():
    import tests.submod.stuff as stuff

    f = stuff.Stuff.propertystuff.fget

    hook, arg = mock.Mock(), mock.Mock()

    with injected_hook(f, hook, arg):
        stuff.Stuff().propertystuff
    stuff.Stuff().propertystuff

    hook.assert_called_once_with(arg)


def test_finally():
    import tests.submod.stuff as stuff

    f = stuff.finallystuff

    hook, arg = mock.Mock(), mock.Mock()

    with injected_hook(f, hook, arg, line=157):
        f()
    f()

    hook.assert_called_once_with(arg)


def test_try_except():
    def target():
        try:
            response = 42
            for a in range(100):
                if a == response:
                    break
        except ValueError as e:
            if not e.args:
                raise

    hook, arg = mock.Mock(), mock.Mock()

    with injected_hook(target, hook, arg, line=target.__code__.co_firstlineno + 1):
        target()

    hook.assert_called_once_with(arg)


def test_for_block():
    def for_loop():
        a = []
        for i in range(10):
            a.append(i)
        return a

    hook, arg = mock.Mock(), mock.Mock()

    with injected_hook(for_loop, hook, arg, line=for_loop.__code__.co_firstlineno + 2):
        for_loop()

    hook.assert_called_once_with(arg)

import dis
import sys
import pytest

from ddtrace.internal.error_reporting.handled_exceptions_by_bytecode import _inject_handled_exception_reporting

skipif_bytecode_injection_not_supported = pytest.mark.skipif(
    sys.version_info[:2] < (3, 10),
    reason="Injection is only supported for 3.10+",
)

TRY = 1
CALLBACK = 2
EXCEPT_VALUEERROR = 4
FINALLY = 8
EXCEPT_NOTIMPLEMENTED = 16
TRY_1 = 32
EXCEPT_VALUEERROR_1 = 64


@skipif_bytecode_injection_not_supported
def test_generic_except():
    value = ''

    def callback(*args):
        nonlocal value
        _, e, _ = sys.exc_info()
        value += str(e)

    def func():
        nonlocal value
        try:
            value += '<try>'
            raise ValueError('<error>')
        except:
            value += '<except>'

    _inject_handled_exception_reporting(func, callback)
    func()

    assert value == '<try><error><except>'


@skipif_bytecode_injection_not_supported
def test_generic_finally():
    value = 0

    def callback(*args):
        nonlocal value
        value |= CALLBACK

    def func():
        nonlocal value
        try:
            value |= TRY
            raise ValueError('value error')
        except:
            value |= EXCEPT_VALUEERROR
        finally:
            value |= FINALLY

    _inject_handled_exception_reporting(func, callback)
    func()

    assert value == TRY + CALLBACK + EXCEPT_VALUEERROR + FINALLY


@skipif_bytecode_injection_not_supported
def test_matched_except():
    value = 0

    def callback(*args):
        nonlocal value
        value |= CALLBACK

    def func():
        nonlocal value
        try:
            value |= TRY
            raise ValueError('value error')
        except ValueError as _:
            value |= EXCEPT_VALUEERROR

    _inject_handled_exception_reporting(func, callback)
    func()

    assert value == TRY + CALLBACK + EXCEPT_VALUEERROR


@skipif_bytecode_injection_not_supported
def test_multiple_try_except_blocks():
    value = ''

    def callback(*args):
        nonlocal value
        _, e, _ = sys.exc_info()
        value += str(e)

    def func():
        nonlocal value
        a = 1
        try:
            value += '<try1>'
            raise ValueError('<error1>')
        except ValueError as _:
            value += '<except1>'
        b = a + 1
        try:
            value += '<try2>'
            raise ValueError('<error2>')
        except ValueError as _:
            value += '<except2>'
        _c = a + b

    _inject_handled_exception_reporting(func, callback)
    func()

    assert value == '<try1><error1><except1><try2><error2><except2>'


@skipif_bytecode_injection_not_supported
def test_matched_multiple_except():
    value = ''

    def callback(*args):
        nonlocal value
        _, e, _ = sys.exc_info()
        value += str(e)

    def func():
        nonlocal value
        try:
            value += '<try>'
            raise ValueError('<error1>')
        except NotImplementedError as _:
            value += '<not implemented>'
        except:
            value += '<except>'

    _inject_handled_exception_reporting(func, callback)
    func()

    assert value == '<try><error1><except>'


@skipif_bytecode_injection_not_supported
def test_matched_finally():
    value = 0

    def callback(*args):
        nonlocal value
        value |= CALLBACK

    def func():
        nonlocal value
        try:
            value |= TRY
            raise ValueError('value error')
        except ValueError as _:
            value |= EXCEPT_VALUEERROR
        finally:
            value |= FINALLY

    _inject_handled_exception_reporting(func, callback)
    func()

    assert value == TRY + CALLBACK + EXCEPT_VALUEERROR + FINALLY


@skipif_bytecode_injection_not_supported
def test_matched_nested():
    value = ''

    def callback(*args):
        nonlocal value
        _, e, _ = sys.exc_info()
        value += str(e)

    def func():
        nonlocal value
        try:
            value += '<try1>'
            raise ValueError('<error1>')
        except ValueError as _1:
            try:
                value += '<try2>'
                raise ValueError('<error2>')
            except ValueError as _2:
                value += '<except2>'
            value += '<except1>'

    _inject_handled_exception_reporting(func, callback)
    func()

    assert value == '<try1><error1><try2><error2><except2><except1>'


@skipif_bytecode_injection_not_supported
def test_matched_external_function():
    value = ''

    def callback(*args):
        nonlocal value
        _, e, _ = sys.exc_info()
        value += str(e)

    def func():
        nonlocal value
        try:
            value += '<try1>'
            a_function_throwing_a_value_error()
        except ValueError as _1:
            value += '<except1>'

    _inject_handled_exception_reporting(func, callback)
    func()

    assert value == '<try1><external><except1>'


@skipif_bytecode_injection_not_supported
def test_matched_finally_multiple_except():
    value = ''

    def callback(*args):
        nonlocal value
        _, e, _ = sys.exc_info()
        value += str(e)

    def func(which: int):
        nonlocal value
        try:
            value += '<try>'
            if which == 1:
                raise NotImplementedError('<not implemented>')
            elif which == 2:
                raise ValueError('<value error>')
            elif which == 3:
                raise Exception('<exception>')
        except NotImplementedError as _:
            value += '<except not implemented>'
        except ValueError as _:
            value += '<except value error>'
        except:
            value += '<except *>'
        finally:
            value += '<finally>'

    _inject_handled_exception_reporting(func, callback)

    value = ''
    func(1)
    assert value == '<try><not implemented><except not implemented><finally>'

    value = ''
    func(2)
    assert value == '<try><value error><except value error><finally>'

    value = ''
    func(3)
    assert value == '<try><exception><except *><finally>'


def a_function_throwing_a_value_error():
    raise ValueError('<external>')

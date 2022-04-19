from types import FunctionType
from typing import Any
from typing import Callable
from typing import List
from typing import Tuple

from bytecode import Bytecode
from bytecode import Instr

from ddtrace.internal.utils.importlib import func_name


_HookType = Callable[[Any], Any]


HOOK_ARG_PREFIX = "_hook_arg"


class InvalidLine(Exception):
    """
    Raised when trying to inject a hook on an invalid line, e.g. a comment or a blank line.
    """


def _inject_hook(code, hook, lineno, arg):
    # type: (Bytecode, _HookType, int, Any) -> None
    for i, instr in enumerate(code):
        try:
            if instr.lineno == lineno:
                # gotcha!
                break
        except AttributeError:
            # pseudo-instruction (e.g. label)
            pass
    else:
        raise InvalidLine("Line %d is blank or a comment line" % lineno)

    # actual injection
    code[i:i] = Bytecode(
        [
            Instr("LOAD_CONST", hook, lineno=lineno),
            Instr("LOAD_CONST", arg, lineno=lineno),
            Instr("CALL_FUNCTION", 1, lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
        ]
    )


def _eject_hook(code, line, arg):
    # type: (Bytecode, int, Any) -> None
    for i, instr in enumerate(code):
        try:
            if (
                instr.lineno == line
                and code[i + 1].arg is arg
                and [code[_].name for _ in range(i, i + 4)] == ["LOAD_CONST", "LOAD_CONST", "CALL_FUNCTION", "POP_TOP"]
            ):
                # gotcha!
                break
        except AttributeError:
            # pseudo-instruction (e.g. label)
            pass
        except IndexError:
            pass
    else:
        raise InvalidLine("Line %d does not contain a hook" % line)

    # actual ejection
    del code[i : i + 4]


def _function_with_new_code(f, abstract_code):
    f.__code__ = abstract_code.to_code()
    return f


def inject_hooks(f, hs):
    # type: (FunctionType, List[Tuple[_HookType, int, Any]]) -> FunctionType
    """Bulk-inject a list of hooks into a function."""
    f_code = f.__code__
    abstract_code = Bytecode.from_code(f_code)

    # sanity check
    assert len(abstract_code), "Abstract code has opcodes"

    lines = {_.lineno for _ in abstract_code if hasattr(_, "lineno")}
    for _, line, _ in hs:
        if line not in lines:
            raise InvalidLine("Invalid line number '%d' for function '%s'" % (line, func_name(f)))

    for h, line, arg in hs:
        _inject_hook(abstract_code, h, line, arg)

    return _function_with_new_code(f, abstract_code)


def eject_hooks(f, ls):
    # type: (FunctionType, List[Tuple[int, Any]]) -> FunctionType
    """Bulk-eject a list of hooks from a function.

    The hooks are identified by their line number and the argument passed to the
    hook.
    """
    f_code = f.__code__
    abstract_code = Bytecode.from_code(f_code)

    # sanity check
    assert len(abstract_code)

    linenos = {_.lineno for _ in abstract_code if hasattr(_, "lineno")}
    for line, _ in ls:
        if line not in linenos:
            raise InvalidLine("Invalid line number '%d' for function '%s'" % (line, func_name(f)))

    for line, arg in ls:
        _eject_hook(abstract_code, line, arg)

    return _function_with_new_code(f, abstract_code)


def inject_hook(f, h, line, arg):
    # type: (FunctionType, _HookType, int, Any) -> FunctionType
    """Inject a hook into a function.

    The hook is injected at the given line number and called with the given
    argument. The latter is also used as an identifier for the hook. This should
    be kept in case the hook needs to be removed.
    """
    return inject_hooks(f, [(h, line, arg)])


def eject_hook(f, line, arg):
    # type: (FunctionType, int, Any) -> FunctionType
    """Eject a hook from a function.

    The hook is identified by its line number and the argument passed to the
    hook.
    """
    return eject_hooks(f, [(line, arg)])

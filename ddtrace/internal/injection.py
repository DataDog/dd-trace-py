from collections import deque
from types import FunctionType
from typing import Any
from typing import Callable
from typing import Deque
from typing import List
from typing import Tuple

from bytecode import Bytecode
from bytecode import Instr

from .compat import PYTHON_VERSION_INFO as PY


HookType = Callable[[Any], Any]
HookInfoType = Tuple[HookType, int, Any]

HOOK_ARG_PREFIX = "_hook_arg"


class InvalidLine(Exception):
    """
    Raised when trying to inject a hook on an invalid line, e.g. a comment or a blank line.
    """


def _inject_hook(code, hook, lineno, arg):
    # type: (Bytecode, HookType, int, Any) -> None
    """Inject a hook at the given line number inside an abstract code object.

    The hook is called with the given argument, which is also used as an
    identifier for the hook itself. This should be kept in case the hook needs
    to be removed.
    """
    # DEV: In general there are no guarantees for bytecode to be "linear",
    # meaning that a line number can occur multiple times. We need to find all
    # occurrences and inject the hook at each of them. An example of when this
    # happens is with finally blocks, which are duplicated at the end of the
    # bytecode.
    locs = deque()  # type: Deque[int]
    last_lineno = None
    for i, instr in enumerate(code):
        try:
            if instr.lineno == last_lineno:
                continue
            last_lineno = instr.lineno
            if instr.lineno == lineno:
                locs.appendleft(i)
        except AttributeError:
            # pseudo-instruction (e.g. label)
            pass

    if not locs:
        raise InvalidLine("Line %d does not exist or is either blank or a comment" % lineno)

    # DEV: This is the bytecode equivalent of
    # >>> hook(arg)
    # Additionally, we must discard the return value (top of the stack) to
    # restore the stack to the state prior to the call.
    for i in locs:
        if PY < (3, 11):
            code[i:i] = Bytecode(
                [
                    Instr("LOAD_CONST", hook, lineno=lineno),
                    Instr("LOAD_CONST", arg, lineno=lineno),
                    Instr("CALL_FUNCTION", 1, lineno=lineno),
                    Instr("POP_TOP", lineno=lineno),
                ]
            )
        elif PY >= (3, 12):
            code[i:i] = Bytecode(
                [
                    Instr("PUSH_NULL", lineno=lineno),
                    Instr("LOAD_CONST", hook, lineno=lineno),
                    Instr("LOAD_CONST", arg, lineno=lineno),
                    Instr("CALL", 1, lineno=lineno),
                    Instr("POP_TOP", lineno=lineno),
                ]
            )
        else:
            # Python 3.11
            code[i:i] = Bytecode(
                [
                    Instr("PUSH_NULL", lineno=lineno),
                    Instr("LOAD_CONST", hook, lineno=lineno),
                    Instr("LOAD_CONST", arg, lineno=lineno),
                    Instr("PRECALL", 1, lineno=lineno),
                    Instr("CALL", 1, lineno=lineno),
                    Instr("POP_TOP", lineno=lineno),
                ]
            )


# Default to Python 3.11 opcodes
_INJECT_HOOK_OPCODES = ["PUSH_NULL", "LOAD_CONST", "LOAD_CONST", "PRECALL", "CALL", "POP_TOP"]
if PY < (3, 11):
    _INJECT_HOOK_OPCODES = ["LOAD_CONST", "LOAD_CONST", "CALL_FUNCTION", "POP_TOP"]
elif PY >= (3, 12):
    _INJECT_HOOK_OPCODES = ["PUSH_NULL", "LOAD_CONST", "LOAD_CONST", "CALL", "POP_TOP"]

_INJECT_HOOK_OPCODE_POS = 0 if PY < (3, 11) else 1
_INJECT_ARG_OPCODE_POS = 1 if PY < (3, 11) else 2


def _eject_hook(code, hook, line, arg):
    # type: (Bytecode, HookType, int, Any) -> None
    """Eject a hook from the abstract code object at the given line number.

    The hook is identified by its argument. This ensures that only the right
    hook is ejected.
    """
    locs = deque()  # type: Deque[int]
    for i, instr in enumerate(code):
        try:
            # DEV: We look at the expected opcode pattern to match the injected
            # hook and we also test for the expected opcode arguments
            if (
                instr.lineno == line
                and code[i + _INJECT_HOOK_OPCODE_POS].arg == hook  # bound methods don't like identity comparisons
                and code[i + _INJECT_ARG_OPCODE_POS].arg is arg
                and [code[_].name for _ in range(i, i + len(_INJECT_HOOK_OPCODES))] == _INJECT_HOOK_OPCODES
            ):
                locs.appendleft(i)
        except AttributeError:
            # pseudo-instruction (e.g. label)
            pass
        except IndexError:
            pass

    if not locs:
        raise InvalidLine("Line %d does not contain a hook" % line)

    for i in locs:
        del code[i : i + len(_INJECT_HOOK_OPCODES)]


def _function_with_new_code(f, abstract_code):
    f.__code__ = abstract_code.to_code()
    return f


def inject_hooks(f, hooks):
    # type: (FunctionType, List[HookInfoType]) -> List[HookInfoType]
    """Bulk-inject a list of hooks into a function.

    Hooks are specified via a list of tuples, where each tuple contains the hook
    itself, the line number and the identifying argument passed to the hook.

    Returns the list of hooks that failed to be injected.
    """
    abstract_code = Bytecode.from_code(f.__code__)

    failed = []
    for hook, line, arg in hooks:
        try:
            _inject_hook(abstract_code, hook, line, arg)
        except InvalidLine:
            failed.append((hook, line, arg))

    if len(failed) < len(hooks):
        _function_with_new_code(f, abstract_code)

    return failed


def eject_hooks(f, hooks):
    # type: (FunctionType, List[HookInfoType]) -> List[HookInfoType]
    """Bulk-eject a list of hooks from a function.

    The hooks are specified via a list of tuples, where each tuple contains the
    hook line number and the identifying argument.

    Returns the list of hooks that failed to be ejected.
    """
    abstract_code = Bytecode.from_code(f.__code__)

    failed = []
    for hook, line, arg in hooks:
        try:
            _eject_hook(abstract_code, hook, line, arg)
        except InvalidLine:
            failed.append((hook, line, arg))

    if len(failed) < len(hooks):
        _function_with_new_code(f, abstract_code)

    return failed


def inject_hook(f, hook, line, arg):
    # type: (FunctionType, HookType, int, Any) -> FunctionType
    """Inject a hook into a function.

    The hook is injected at the given line number and called with the given
    argument. The latter is also used as an identifier for the hook. This should
    be kept in case the hook needs to be removed.
    """
    abstract_code = Bytecode.from_code(f.__code__)

    _inject_hook(abstract_code, hook, line, arg)

    return _function_with_new_code(f, abstract_code)


def eject_hook(f, hook, line, arg):
    # type: (FunctionType, HookType, int, Any) -> FunctionType
    """Eject a hook from a function.

    The hook is identified by its line number and the argument passed to the
    hook.
    """
    abstract_code = Bytecode.from_code(f.__code__)

    _eject_hook(abstract_code, hook, line, arg)

    return _function_with_new_code(f, abstract_code)

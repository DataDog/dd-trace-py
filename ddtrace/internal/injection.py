from types import FunctionType
from typing import Any
from typing import Callable
from typing import List
from typing import Tuple

from bytecode import Bytecode
from bytecode import Instr


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
    for i, instr in enumerate(code):
        try:
            if instr.lineno == lineno:
                # gotcha!
                break
        except AttributeError:
            # pseudo-instruction (e.g. label)
            pass
    else:
        raise InvalidLine("Line %d does not exist or is either blank or a comment" % lineno)

    # DEV: This is the bytecode equivalent of
    # >>> hook(arg)
    # Additionally, we must discard the return value (top of the stack) to
    # restore the stack to the state prior to the call.
    code[i:i] = Bytecode(
        [
            Instr("LOAD_CONST", hook, lineno=lineno),
            Instr("LOAD_CONST", arg, lineno=lineno),
            Instr("CALL_FUNCTION", 1, lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
        ]
    )


def _eject_hook(code, hook, line, arg):
    # type: (Bytecode, HookType, int, Any) -> None
    """Eject a hook from the abstract code object at the given line number.

    The hook is identified by its argument. This ensures that only the right
    hook is ejected.
    """
    for i, instr in enumerate(code):
        try:
            # DEV: We look at the expected opcode pattern to match the injected
            # hook and we also test for the expected opcode arguments
            if (
                instr.lineno == line
                and code[i].arg == hook  # bound methods don't like identity comparisons
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

    del code[i : i + 4]


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

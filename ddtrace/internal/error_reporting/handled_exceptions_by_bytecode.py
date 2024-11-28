import dis
import sys
from types import CodeType
import typing as t

from ..bytecode_injection.core import InjectionContext
from ..bytecode_injection.core import inject_invocation
from .hook import _default_datadog_exc_callback


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 10)  # and sys.version_info < (3, 12)  # nosec


def _inject_handled_exception_reporting(func):
    func = func.__wrapped__ if hasattr(func, "__wrapped__") else func
    original_code = func.__code__  # type: CodeType

    injection_indexes = _find_bytecode_indexes(original_code)
    if not injection_indexes:
        return ()

    def injection_lines_cb(_: InjectionContext):
        return [opcode for opcode, _ in dis.findlinestarts(original_code) if opcode in injection_indexes]

    injection_context = InjectionContext(original_code, _default_datadog_exc_callback, injection_lines_cb)

    code, _ = inject_invocation(injection_context, "path/to/file.py", "my.package")
    func.__code__ = code


WAITING_FOR_EXC = 0
CHECKING_EXC_MATCH = 1
ANY_EXC_HANDLING_BLOCK = 2
MATCHED_EXC_HANDLING_BLOCK = 3

PUSH_EXC_INFO = dis.opmap["PUSH_EXC_INFO"]
CHECK_EXC_MATCH = dis.opmap["CHECK_EXC_MATCH"]


def _find_bytecode_indexes(code: CodeType) -> t.List[int]:
    injection_indexes = []
    state = WAITING_FOR_EXC

    exc_entries = dis._parse_exception_table(code)
    for exc_entry in exc_entries:
        # we are currently not supporting exc table entry targets as instructions
        # if (
        #     isinstance(exc_entry.target, Instruction)
        #     or isinstance(exc_entry.start, Instruction)
        #     or isinstance(exc_entry.end, Instruction)
        # ):
        #     break

        if state == WAITING_FOR_EXC and code.co_code[exc_entry.target] == PUSH_EXC_INFO:
            # at this point either the exception is checked for a match, for example
            #   ...except >>>ValueError as e<<<:
            # or the exception handling code starts, for example
            #   ...except:
            #          print('...')
            state = CHECKING_EXC_MATCH
            continue

        if state == CHECKING_EXC_MATCH:
            if CHECK_EXC_MATCH in code.co_code[exc_entry.start : exc_entry.end : 2]:
                # we need to move forward, because this block of code is just checking
                # if the exception handled matches the one that was raised
                state = MATCHED_EXC_HANDLING_BLOCK
                continue
            else:
                state = ANY_EXC_HANDLING_BLOCK

        if state == ANY_EXC_HANDLING_BLOCK:
            injection_indexes.append(exc_entry.start + 2)
            state = WAITING_FOR_EXC
        elif state == MATCHED_EXC_HANDLING_BLOCK:
            injection_indexes.append(exc_entry.start)
            state = WAITING_FOR_EXC

    return injection_indexes

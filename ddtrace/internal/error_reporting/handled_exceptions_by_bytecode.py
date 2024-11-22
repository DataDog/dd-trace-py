import dis
import sys
from types import CodeType
import typing as t
from .hook import _default_datadog_exc_callback
from ..bytecode_injection.core import inject_invocation

# from ddtrace.internal.coverage.instrumentation_py3_11 import NO_OFFSET
# from ddtrace.internal.coverage.instrumentation_py3_11 import SKIP_LINES
# from ddtrace.internal.coverage.instrumentation_py3_11 import InjectionContext
# from ddtrace.internal.coverage.instrumentation_py3_11 import Instruction
# from ddtrace.internal.coverage.instrumentation_py3_11 import inject_instructions
# from ddtrace.internal.coverage.instrumentation_py3_11 import instr_with_arg
# from ddtrace.internal.coverage.instrumentation_py3_11 import parse_exception_table


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 10)  # and sys.version_info < (3, 12)  # nosec


def _inject_handled_exception_reporting(func):
    func = func.__wrapped__ if hasattr(func, "__wrapped__") else func
    original_code = func.__code__

    injection_indexes = _find_bytecode_indexes(original_code)
    if not injection_indexes:
        return ()

    injection_context = InjectionContext.from_code(func.__code__, "put.the.package.here")
    injection_context.instructions_cb = generate_instructions

    injection_lines = {
        opcode: line for opcode, line in dis.findlinestarts(original_code) if opcode in injection_indexes
    }

    code, _ = inject_instructions(injection_context, injection_lines, SKIP_LINES)
    func.__code__ = code

# def generate_instructions(injection_context: InjectionContext, line_numbe: int) -> t.Tuple[Instruction, ...]:
#     _0_idx = injection_context.with_const(0)
#     cb_invocation_idx = injection_context.with_const(("_default_datadog_exc_callback",))

#     cb_module_idx = injection_context.with_name("ddtrace.internal.error_reporting.hook")
#     cb_name_idx = injection_context.with_name("_default_datadog_exc_callback")

#     cb_var_idx = injection_context.with_varname("_default_datadog_exc_callback")

#     precall_instructions = (
#         [Instruction(NO_OFFSET, dis.opmap["PRECALL"], 0), Instruction(NO_OFFSET, dis.opmap["CACHE"], 0)]
#         if sys.version_info[:2] == (3, 11)
#         else []
#     )

#     instructions = [
#         Instruction(NO_OFFSET, dis.opmap["LOAD_CONST"], _0_idx),
#         *instr_with_arg(dis.opmap["LOAD_CONST"], cb_invocation_idx),
#         *instr_with_arg(dis.opmap["IMPORT_NAME"], cb_module_idx),
#         *instr_with_arg(dis.opmap["IMPORT_FROM"], cb_name_idx),
#         Instruction(NO_OFFSET, dis.opmap["STORE_FAST"], cb_var_idx),
#         Instruction(NO_OFFSET, dis.opmap["POP_TOP"], 0),
#         Instruction(NO_OFFSET, dis.opmap["PUSH_NULL"], 0),
#         Instruction(NO_OFFSET, dis.opmap["LOAD_FAST"], cb_var_idx),
#         *precall_instructions,
#         Instruction(NO_OFFSET, dis.opmap["CALL"], 0),
#         Instruction(NO_OFFSET, dis.opmap["CACHE"], 0),
#         Instruction(NO_OFFSET, dis.opmap["CACHE"], 0),
#         Instruction(NO_OFFSET, dis.opmap["CACHE"], 0),
#         Instruction(NO_OFFSET, dis.opmap["CACHE"], 0),
#         Instruction(NO_OFFSET, dis.opmap["POP_TOP"], 0),
#     ]

#     return tuple(instructions)


# def _inject_handled_exception_reporting(func):
#     func = func.__wrapped__ if hasattr(func, "__wrapped__") else func
#     original_code = func.__code__

#     injection_indexes = _find_bytecode_indexes(original_code)
#     if not injection_indexes:
#         return ()

#     injection_context = InjectionContext.from_code(func.__code__, "put.the.package.here")
#     injection_context.instructions_cb = generate_instructions

#     injection_lines = {
#         opcode: line for opcode, line in dis.findlinestarts(original_code) if opcode in injection_indexes
#     }

#     code, _ = inject_instructions(injection_context, injection_lines, SKIP_LINES)
#     func.__code__ = code


WAITING_FOR_EXC = 0
CHECKING_EXC_MATCH = 1
ANY_EXC_HANDLING_BLOCK = 2
MATCHED_EXC_HANDLING_BLOCK = 3

PUSH_EXC_INFO = dis.opmap["PUSH_EXC_INFO"]
CHECK_EXC_MATCH = dis.opmap["CHECK_EXC_MATCH"]


def _find_bytecode_indexes(code: CodeType) -> t.List[int]:
    """
    TODO: Move it to use __code__'s exceptions table
    """
    injection_indexes = []
    state = WAITING_FOR_EXC

    exc_entries = parse_exception_table(code)
    for exc_entry in exc_entries:
        # we are currently not supporting exc table entry targets as instructions
        if (
            isinstance(exc_entry.target, Instruction)
            or isinstance(exc_entry.start, Instruction)
            or isinstance(exc_entry.end, Instruction)
        ):
            break

        if state == WAITING_FOR_EXC and code.co_code[exc_entry.target] == PUSH_EXC_INFO:
            # at this point either the exception is checked for a match, for example
            #   ...except >>>ValueError as e<<<:
            # or the exception handling code starts, for example
            #   ...except:
            #          print('...')
            state = CHECKING_EXC_MATCH
            continue

        if state == CHECKING_EXC_MATCH:
            if CHECK_EXC_MATCH in code.co_code[exc_entry.start: exc_entry.end: 2]:
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

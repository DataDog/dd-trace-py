import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.coverage.instrumentation_py3_11 import NO_OFFSET
from ddtrace.internal.coverage.instrumentation_py3_11 import SKIP_LINES
from ddtrace.internal.coverage.instrumentation_py3_11 import InjectionContext
from ddtrace.internal.coverage.instrumentation_py3_11 import Instruction
from ddtrace.internal.coverage.instrumentation_py3_11 import inject_instructions
from ddtrace.internal.coverage.instrumentation_py3_11 import instr_with_arg
from ddtrace.internal.coverage.instrumentation_py3_11 import parse_exception_table


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 11)  # and sys.version_info < (3, 12)  # nosec


def generate_instructions(injection_context: InjectionContext, line_numbe: int) -> t.Tuple[Instruction, ...]:
    _0_idx = injection_context.with_const(0)
    cb_invocation_idx = injection_context.with_const(("_default_datadog_exc_callback",))

    cb_module_idx = injection_context.with_name("ddtrace.internal.error_reporting.handled_exceptions")
    cb_name_idx = injection_context.with_name("_default_datadog_exc_callback")

    cb_var_idx = injection_context.with_varname("_default_datadog_exc_callback")

    precall_instructions = (
        [Instruction(NO_OFFSET, dis.opmap["PRECALL"], 0), Instruction(NO_OFFSET, dis.opmap["CACHE"], 0)]
        if sys.version_info[:2] == (3, 11)
        else []
    )

    instructions = [
        Instruction(NO_OFFSET, dis.opmap["LOAD_CONST"], _0_idx),
        *instr_with_arg(dis.opmap["LOAD_CONST"], cb_invocation_idx),
        *instr_with_arg(dis.opmap["IMPORT_NAME"], cb_module_idx),
        *instr_with_arg(dis.opmap["IMPORT_FROM"], cb_name_idx),
        Instruction(NO_OFFSET, dis.opmap["STORE_FAST"], cb_var_idx),
        Instruction(NO_OFFSET, dis.opmap["POP_TOP"], 0),
        Instruction(NO_OFFSET, dis.opmap["PUSH_NULL"], 0),
        Instruction(NO_OFFSET, dis.opmap["LOAD_FAST"], cb_var_idx),
        *precall_instructions,
        Instruction(NO_OFFSET, dis.opmap["CALL"], 0),
        Instruction(NO_OFFSET, dis.opmap["CACHE"], 0),
        Instruction(NO_OFFSET, dis.opmap["CACHE"], 0),
        Instruction(NO_OFFSET, dis.opmap["CACHE"], 0),
        Instruction(NO_OFFSET, dis.opmap["CACHE"], 0),
        Instruction(NO_OFFSET, dis.opmap["POP_TOP"], 0),
    ]

    return tuple(instructions)


def _inject_handled_exception_reporting(func):
    func = func.__wrapped__ if hasattr(func, "__wrapped__") else func
    original_code = func.__code__

    # precall_instructions = (
    #     [Instruction(NO_OFFSET, dis.opmap["PRECALL"], 0), Instruction(NO_OFFSET, dis.opmap["CACHE"], 0)]
    #     if sys.version_info[:2] == (3, 11)
    #     else []
    # )

    # instructions = [
    #     Instruction(NO_OFFSET, LOAD_CONST, _0_idx),
    #     *instr_with_arg(LOAD_CONST, cb_invocation_idx),
    #     *instr_with_arg(IMPORT_NAME, cb_module_idx),
    #     *instr_with_arg(IMPORT_FROM, cb_name_idx),
    #     Instruction(NO_OFFSET, STORE_FAST, cb_var_idx),
    #     Instruction(NO_OFFSET, POP_TOP, 0),
    #     Instruction(NO_OFFSET, PUSH_NULL, 0),
    #     Instruction(NO_OFFSET, LOAD_FAST, cb_var_idx),
    #     *precall_instructions,
    #     Instruction(NO_OFFSET, CALL, 0),
    #     Instruction(NO_OFFSET, CACHE, 0),
    #     Instruction(NO_OFFSET, CACHE, 0),
    #     Instruction(NO_OFFSET, CACHE, 0),
    #     Instruction(NO_OFFSET, CACHE, 0),
    #     Instruction(NO_OFFSET, POP_TOP, 0),
    # ]

    # injection_indexes = _find_bytecode_indexes(original_code)
    # if not injection_indexes:
    #     return

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


# def _generate_instructions(cb_index: int, arg_index: int) -> t.Tuple[Instruction, ...]:

#     precall_instructions = (
#         [Instruction(NO_OFFSET, dis.opmap["PRECALL"], 0), Instruction(NO_OFFSET, dis.opmap["CACHE"], 0)]
#         if sys.version_info[:2] == (3, 11)
#         else []
#     )

#     instructions = [
#         # Instruction(NO_OFFSET, LOAD_CONST, _0_idx),
#         # *instr_with_arg(LOAD_CONST, cb_invocation_idx),
#         # *instr_with_arg(IMPORT_NAME, cb_module_idx),
#         # *instr_with_arg(IMPORT_FROM, cb_name_idx),
#         # Instruction(NO_OFFSET, STORE_FAST, cb_var_idx),
#         # Instruction(NO_OFFSET, POP_TOP, 0),
#         # Instruction(NO_OFFSET, PUSH_NULL, 0),
#         # Instruction(NO_OFFSET, LOAD_FAST, cb_var_idx),

#         Instruction(NO_OFFSET, PUSH_NULL, 0),
#         *instr_with_arg(LOAD_CONST, cb_index),
#         *instr_with_arg(LOAD_CONST, arg_index),

#         *precall_instructions,
#         Instruction(NO_OFFSET, CALL, 0),
#         Instruction(NO_OFFSET, CACHE, 0),
#         Instruction(NO_OFFSET, CACHE, 0),
#         Instruction(NO_OFFSET, CACHE, 0),
#         Instruction(NO_OFFSET, CACHE, 0),
#         Instruction(NO_OFFSET, POP_TOP, 0),
#     ]
#     return tuple(instructions)


# def _inject_handled_exception_reporting_alt_impl(func):
#     func = func.__wrapped__ if hasattr(func, "__wrapped__") else func
#     original_code = func.__code__

#     consts = list(original_code.co_consts)
#     _0_idx, cb_invocation_idx = inject_co_consts(consts, 0, ("_default_datadog_exc_callback",))

#     names = list(original_code.co_names)
#     cb_module_idx, cb_name_idx = inject_co_names(
#         names, "ddtrace.internal.error_reporting.handled_exceptions", "_default_datadog_exc_callback"
#     )

#     variables = list(original_code.co_varnames)
#     (cb_var_idx,) = inject_co_varnames(variables, "_default_datadog_exc_callback")

#     precall_instructions = (
#         [Instruction(NO_OFFSET, dis.opmap["PRECALL"], 0), Instruction(NO_OFFSET, dis.opmap["CACHE"], 0)]
#         if sys.version_info[:2] == (3, 11)
#         else []
#     )

#     instructions = [
#         Instruction(NO_OFFSET, LOAD_CONST, _0_idx),
#         *instr_with_arg(LOAD_CONST, cb_invocation_idx),
#         *instr_with_arg(IMPORT_NAME, cb_module_idx),
#         *instr_with_arg(IMPORT_FROM, cb_name_idx),
#         Instruction(NO_OFFSET, STORE_FAST, cb_var_idx),
#         Instruction(NO_OFFSET, POP_TOP, 0),
#         Instruction(NO_OFFSET, PUSH_NULL, 0),
#         Instruction(NO_OFFSET, LOAD_FAST, cb_var_idx),
#         *precall_instructions,
#         Instruction(NO_OFFSET, CALL, 0),
#         Instruction(NO_OFFSET, CACHE, 0),
#         Instruction(NO_OFFSET, CACHE, 0),
#         Instruction(NO_OFFSET, CACHE, 0),
#         Instruction(NO_OFFSET, CACHE, 0),
#         Instruction(NO_OFFSET, POP_TOP, 0),
#     ]

#     injection_indexes = _find_bytecode_indexes(original_code)
#     if not injection_indexes:
#         return

#     # for injection_index in reversed(injection_indexes):
#     #     new_code_b = (
#     #         original_code.co_code[:injection_index]
#     #         + instructions_to_bytecode(instructions_3_11)
#     #         + original_code.co_code[injection_index:]
#     #     )
#     #     func.__code__ = original_code.replace(
#     #         co_code=new_code_b,
#     #         co_consts=tuple(consts),
#     #         co_names=tuple(names),
#     #         co_varnames=tuple(variables),
#     #         co_nlocals=len(variables),
#     #     )
#     code_for_injection = original_code.replace(
#         co_consts=tuple(consts),
#         co_names=tuple(names),
#         co_varnames=tuple(variables),
#         co_nlocals=len(variables),
#     )

#     new_code_b = inject_instructions(code_for_injection, instructions, injection_indexes)
#     func.__code__ = new_code_b


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

import dis
import sys
import typing as t
from types import CodeType
from ddtrace.internal.instrumentation.core import Instruction, NO_OFFSET, instr_with_arg, inject_co_consts, inject_co_names, instructions_to_bytecode, inject_co_varnames, parse_exception_table
from ddtrace.internal.instrumentation.opcodes import *

# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 11)  # and sys.version_info < (3, 12)  # nosec


def _inject_handled_exception_reporting(func):
    func = func.__wrapped__ if hasattr(func, '__wrapped__') else func
    original_code = func.__code__

    consts = list(original_code.co_consts)
    _0_idx, cb_invocation_idx = inject_co_consts(consts, 0, ('_default_datadog_exc_callback',))

    names = list(original_code.co_names)
    cb_module_idx, cb_name_idx = inject_co_names(
        names, 'ddtrace.internal.error_reporting.handled_exceptions', '_default_datadog_exc_callback')

    variables = list(original_code.co_varnames)
    cb_var_idx, = inject_co_varnames(variables, '_default_datadog_exc_callback')

    instructions_3_11 = [
        Instruction(NO_OFFSET, POP_TOP, 0),
        Instruction(NO_OFFSET, LOAD_CONST, _0_idx),
        *instr_with_arg(LOAD_CONST, cb_invocation_idx),
        *instr_with_arg(IMPORT_NAME, cb_module_idx),
        *instr_with_arg(IMPORT_FROM, cb_name_idx),
        Instruction(NO_OFFSET, STORE_FAST, cb_var_idx),
        Instruction(NO_OFFSET, POP_TOP, 0),
        Instruction(NO_OFFSET, PUSH_NULL, 0),
        Instruction(NO_OFFSET, LOAD_FAST, cb_var_idx),
        Instruction(NO_OFFSET, CALL, 0),
        Instruction(NO_OFFSET, CACHE, 0),
        Instruction(NO_OFFSET, CACHE, 0),
        Instruction(NO_OFFSET, CACHE, 0),
        Instruction(NO_OFFSET, CACHE, 0),
    ]

    injection_indexes = _find_bytecode_indexes(original_code)
    if not injection_indexes:
        return

    for injection_index in reversed(injection_indexes):
        new_code_b = original_code.co_code[:injection_index] + \
            instructions_to_bytecode(instructions_3_11) + original_code.co_code[injection_index:]
        func.__code__ = original_code.replace(co_code=new_code_b, co_consts=tuple(
            consts), co_names=tuple(names), co_varnames=tuple(variables), co_nlocals=len(variables))


WAITING_FOR_EXC = 0
CHECKING_EXC_MATCH = 1
ANY_EXC_HANDLING_BLOCK = 2
MATCHED_EXC_HANDLING_BLOCK = 3


def _find_bytecode_indexes(code: CodeType) -> t.List[int]:
    """
    TODO: Move it to use __code__'s exceptions table
    """
    injection_indexes = []
    state = WAITING_FOR_EXC

    exc_entries = parse_exception_table(code)
    for exc_entry in exc_entries:
        # we are currently not supporting exc table entry targets as instructions
        if isinstance(exc_entry.target, Instruction) \
                or isinstance(exc_entry.start, Instruction) \
                or isinstance(exc_entry.end, Instruction):
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
            if CHECK_EXC_MATCH in code.co_code[exc_entry.start:exc_entry.end:2]:
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

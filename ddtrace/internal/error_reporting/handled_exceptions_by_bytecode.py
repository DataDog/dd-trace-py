import sys
from types import CodeType
from ddtrace.internal.instrumentation.core import Instruction, NO_OFFSET, instr_with_arg, inject_co_consts, inject_co_names, instructions_to_bytecode, inject_co_varnames
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

    injection_index = _find_bytecode_index(original_code)
    if not injection_index:
        return

    new_code_b = original_code.co_code[:injection_index] + \
        instructions_to_bytecode(instructions_3_11) + original_code.co_code[injection_index:]

    func.__code__ = original_code.replace(co_code=new_code_b, co_consts=tuple(
        consts), co_names=tuple(names), co_varnames=tuple(variables), co_nlocals=len(variables))


def _find_bytecode_index(code: CodeType):
    """
    TODO: Move it to use __code__'s exceptions table
    """

    for idx in range(0, len(code.co_code), 2):
        instr = code.co_code[idx]
        if instr == PUSH_EXC_INFO:
            return idx + 2  # +2 to inject after the subsequent argument
    return None

import sys
import typing as t
from bytecode import Instr, Bytecode
from ddtrace.internal.instrumentation.core import Instruction, NO_OFFSET, instr_with_arg, inject_consts
from ddtrace.internal.instrumentation.opcodes import *

# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 11)  # and sys.version_info < (3, 12)  # nosec


def _inject_handled_exception_reporting(func):
    func = func.__wrapped__ if hasattr(func, '__wrapped__') else func
    # original_code = func.__code__

    # clb_module_idx, clb_name_idx = inject_consts(
    #     original_code, 'ddtrace.internal.error_reporting.handled_exceptions', '_default_datadog_exc_callback')

    # instructions = [
    #     Instruction(NO_OFFSET, LOAD_CONST, 0),
    #     *instr_with_arg(IMPORT_NAME, clb_module_idx),
    #     *instr_with_arg(IMPORT_FROM, clb_name_idx),
    #     Instruction(NO_OFFSET, STORE_FAST, 0),
    #     Instruction(NO_OFFSET, POP_TOP, 0),
    #     Instruction(NO_OFFSET, PUSH_NULL, 0),
    #     Instruction(NO_OFFSET, LOAD_FAST, 0),
    #     Instruction(NO_OFFSET, CALL, 0),
    #     Instruction(NO_OFFSET, CACHE, 0),
    #     Instruction(NO_OFFSET, CACHE, 0),
    #     Instruction(NO_OFFSET, CACHE, 0),
    #     Instruction(NO_OFFSET, CACHE, 0),
    #     Instruction(NO_OFFSET, POP_TOP, 0),
    # ]

    bcode = Bytecode.from_code(func.__code__)
    injection_index = _find_bytecode_index(bcode)
    if not injection_index:
        return

    injected_instructions_3_11 = [
        # loading the callback from namespace
        Instr("LOAD_CONST", 0),
        Instr("LOAD_CONST", ('_default_datadog_exc_callback', )),  # type: ignore
        Instr("IMPORT_NAME", 'ddtrace.internal.error_reporting.handled_exceptions'),
        Instr("IMPORT_FROM", '_default_datadog_exc_callback'),
        Instr("STORE_FAST", '_default_datadog_exc_callback'),
        Instr("POP_TOP"),
        # invoking the callback
        Instr("PUSH_NULL"),
        Instr("LOAD_FAST", '_default_datadog_exc_callback'),
        # Instr("PRECALL", 0),  # Not necessary on 3.11! Seems not supported on 3.12!
        Instr("CALL", 0),
        Instr("POP_TOP"),
    ]

    injected_instructions = injected_instructions_3_11

    for instr in reversed(injected_instructions):
        bcode.insert(injection_index, instr)

    code = bcode.to_code()

    func.__code__ = code


def _find_bytecode_index(bcode: Bytecode):
    """
    TODO: Move it to use __code__'s exceptions table
    """
    for idx in range(0, len(bcode)):
        instr = bcode[idx]
        if type(instr) == Instr and instr.name == 'PUSH_EXC_INFO':
            return idx + 1  # +1 to inject after the subsequent POP_TOP
    return None

import sys

from bytecode import Instr, Bytecode

# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 11) and sys.version_info < (3, 12)  # nosec


def _inject_handled_exception_reporting(func):
    func = func.__wrapped__ if hasattr(func, '__wrapped__') else func
    bcode = Bytecode.from_code(func.__code__)
    injection_index = _find_bytecode_index(bcode)
    if not injection_index:
        return

    injected_instructions_3_12 = [
        Instr("LOAD_GLOBAL", (True, '_default_datadog_exc_callback')),
        Instr("CALL", 0),
        Instr("POP_TOP"),
    ]

    injected_instructions_3_11 = [
        # loading the callback from namespace
        Instr("LOAD_CONST", 0),
        Instr("LOAD_CONST", ('_default_datadog_exc_callback', )),
        Instr("IMPORT_NAME", 'ddtrace.internal.error_reporting.handled_exceptions'),
        Instr("IMPORT_FROM", '_default_datadog_exc_callback'),
        Instr("STORE_FAST", '_default_datadog_exc_callback'),
        Instr("POP_TOP"),
        # invoking the callback
        Instr("PUSH_NULL"),
        Instr("LOAD_FAST", '_default_datadog_exc_callback'),
        Instr("PRECALL", 0),
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

import dis
import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection.core import CallbackType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.logger import get_logger

from .callbacks import _default_bytecode_exc_callback


log = get_logger(__name__)

py_version = sys.version_info[:2]
if py_version == (3, 10):

    def get_offsets_3_10(_s):
        return [o for o in _find_except_bytecode_indexes_3_10(_s.original_code)]

    offsets_callback = get_offsets_3_10
elif py_version == (3, 11):

    def get_offsets_3_11(_s):
        return [o for o in _find_except_bytecode_indexes_3_11(_s.original_code)]

    offsets_callback = get_offsets_3_11
else:

    def get_offsets_default(_s):
        return []

    offsets_callback = get_offsets_default


def _inject_handled_exception_reporting(func, callback: t.Optional[CallbackType] = None):
    """Find the bytecode offsets for which we should inject our callback
    and call the bytecode injection code
    """

    # If the function has the wrapper, the code we want to instrument is the wrapped code
    code_to_instr = func.__wrapped__ if hasattr(func, "__wrapped__") else func
    if "__code__" not in dir(code_to_instr):
        return

    original_code = code_to_instr.__code__  # type: CodeType

    callback = callback or _default_bytecode_exc_callback

    # Find the bytecode offsets, they must be the first offset of a line as we inject
    # bytecodes only at line start.
    injection_context = InjectionContext(original_code, callback, offsets_callback)

    # Bytecode injection and code replacement
    code, _ = inject_invocation(injection_context, original_code.co_filename, "my.package")

    # While testing, I found an app where we were arriving on a read-only __code__.
    # The try/except is here to prevent crashing on these cases
    try:
        code_to_instr.__code__ = code
    except Exception:
        log.debug("Could not set the code of %s", code_to_instr, exc_info=True)


def _find_except_bytecode_indexes_3_10(code: CodeType) -> t.List[int]:
    """Find the offset of the starting line after the except keyword for Python3.10

    They are two ways of detecting an except in the bytecodes:
    - a simple except: is encoded like that:
    line_number    >>   10 POP_TOP
                        12 POP_TOP
                        14 POP_TOP
    The struct we are looking for is a group of POP_TOP

    - except ValueError as e: is encoded like that:
    line_number    >>   10 DUP_TOP
                        12 LOAD_GLOBAL              0 (ValueError)
                        14 JUMP_IF_NOT_EXC_MATCH    27 (to 54)
                        16 POP_TOP
                        18 STORE_FAST               0 (e)
                        20 POP_TOP
                        22 SETUP_FINALLY           11 (to 46)

    The struct we are looking for is DUP_TOP:LOAD_GLOBAL_JUMP_IF_NOT_EXC_MATCH

    Both opcodes struct are indicated by an instruction looking like:
    0 SETUP_FINALLY            4 (to 10)

    or a JUMP_IF_NOT_EXC_MATCH for a chain of excepts
    """
    DUP_TOP = dis.opmap["DUP_TOP"]
    JUMP_IF_NOT_EXC_MATCH = dis.opmap["JUMP_IF_NOT_EXC_MATCH"]
    POP_TOP = dis.opmap["POP_TOP"]
    LOAD_GLOBAL = dis.opmap["LOAD_GLOBAL"]
    SETUP_FINALLY = dis.opmap["SETUP_FINALLY"]

    injection_indexes = set()

    lines_offsets = [o for o, _ in dis.findlinestarts(code)]

    def inject_next_start_of_line_offset(offset: int):
        """Find the first offset of the next line"""
        while offset not in lines_offsets:
            offset += 2
            if offset > lines_offsets[-1]:
                # we were on the last line, we cannot instrument
                return
        injection_indexes.add(offset)

    def first_offset_not_matching(start: int, *opcodes: int):
        while code.co_code[start] in opcodes:
            start += 2
        return start

    potential_marks = set()

    co_code = code.co_code
    for idx in range(0, len(code.co_code), 2):
        current_opcode = co_code[idx]
        current_arg = co_code[idx + 1]
        # JUMP_IF_NOT_EXC_MATCH can indicate a potential except
        if current_opcode == JUMP_IF_NOT_EXC_MATCH:
            potential_marks.add((current_arg << 1))
            continue

        if idx in potential_marks:
            if current_opcode == DUP_TOP:
                if co_code[idx + 2] == LOAD_GLOBAL and co_code[idx + 4] == JUMP_IF_NOT_EXC_MATCH:
                    inject_next_start_of_line_offset(idx + 6)

            elif current_opcode == POP_TOP:
                # an except block is always the first opcode of a line
                # while finally has the same structure but has no line
                if idx in lines_offsets:
                    inject_next_start_of_line_offset(first_offset_not_matching(idx, POP_TOP))
            continue

        # SETUP_FINALLY can indicate a potential except
        if current_opcode == SETUP_FINALLY:
            target = idx + (current_arg << 1) + 2
            potential_marks.add(target)
            continue

    return sorted(list(injection_indexes))


def _find_except_bytecode_indexes_3_11(code: CodeType) -> t.List[int]:
    """Find the offset of the starting line after the except keyword for Python3.11

    They are two ways of detecting an except in the bytecodes:
    - a simple except: is encoded like that:
                    >>   34 PUSH_EXC_INFO

    line_number          36 POP_TOP
    The struct we are looking for is PUSH_EXC_INFO

    - except ValueError as e: is encoded like that:
    line_number         36 LOAD_GLOBAL              0 (ValueError)
                        48 CHECK_EXC_MATCH
                        50 POP_JUMP_FORWARD_IF_FALSE    26 (to 104)
                        52 STORE_FAST               0 (e)
    The struct we are looking for CHECK_EXC_MATCH followed by POP_JUMP_FORWARD_IF_FALSE
    """
    CACHE = dis.opmap["CACHE"]
    CHECK_EXC_MATCH = dis.opmap["CHECK_EXC_MATCH"]
    POP_JUMP_FORWARD_IF_FALSE = dis.opmap["POP_JUMP_FORWARD_IF_FALSE"]
    POP_TOP = dis.opmap["POP_TOP"]
    PUSH_EXC_INFO = dis.opmap["PUSH_EXC_INFO"]

    lines_offsets = [o for o, _ in dis.findlinestarts(code)]

    def inject_next_start_of_line_offset(offset: int):
        """Find the first offset of the next line"""
        while offset not in lines_offsets:
            offset += 2
            if offset > lines_offsets[-1]:
                # we were on the last line, we cannot instrument
                return
        injection_indexes.add(offset)

    def first_offset_not_matching(start: int, *opcodes: int):
        while code.co_code[start] in opcodes:
            start += 2
        return start

    def nth_non_cache_opcode(start, n):
        for _ in range(n - 1):
            while code.co_code[start + 2] == CACHE:
                start += 2
            start += 2
        return co_code[start]

    injection_indexes: t.Set[int] = set()
    co_code = code.co_code
    for idx in range(0, len(code.co_code), 2):
        # Typed exception handlers
        if co_code[idx] == CHECK_EXC_MATCH and co_code[idx + 2] == POP_JUMP_FORWARD_IF_FALSE:
            inject_next_start_of_line_offset(idx + 6)
        # Generic exception handlers
        elif co_code[idx] == PUSH_EXC_INFO and CHECK_EXC_MATCH != nth_non_cache_opcode(idx, 3):
            inject_next_start_of_line_offset(first_offset_not_matching(idx + 2, POP_TOP, CACHE))

    return sorted(list(injection_indexes))

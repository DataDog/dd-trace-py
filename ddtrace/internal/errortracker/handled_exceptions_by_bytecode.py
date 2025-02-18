import dis
import sys
from types import CodeType
import typing as t

from ..bytecode_injection.core import CallbackType
from ..bytecode_injection.core import InjectionContext
from ..bytecode_injection.core import inject_invocation
from .hook import _default_datadog_exc_callback


def _inject_handled_exception_reporting(func, callback: CallbackType | None = None):
    code_to_instr = func.__wrapped__ if hasattr(func, "__wrapped__") else func
    if "__code__" not in dir(func):
        return

    original_code = code_to_instr.__code__  # type: CodeType

    callback = callback or _default_datadog_exc_callback

    # find the bytecode corresponding to the first instruction after except block
    if sys.version_info[:2] == (3, 10):
        injection_indexes = _find_except_bytecode_indexes_3_10(original_code)
    elif sys.version_info[:2] == (3, 11):
        injection_indexes = _find_except_bytecode_indexes_3_11(original_code)
    else:
        raise NotImplementedError(f"Unsupported python version: {sys.version_info}")

    if not injection_indexes:
        return

    def injection_lines_cb(_: InjectionContext):
        # injection can only occur at the start of a line
        return [opcode for opcode, _ in dis.findlinestarts(original_code) if opcode in injection_indexes]

    injection_context = InjectionContext(original_code, callback, injection_lines_cb)

    code, _ = inject_invocation(injection_context, original_code.co_filename, "my.package")
    code_to_instr.__code__ = code


def _find_except_bytecode_indexes_3_10(code: CodeType) -> t.List[int]:
    DUP_TOP = dis.opmap["DUP_TOP"]
    JUMP_IF_NOT_EXC_MATCH = dis.opmap["JUMP_IF_NOT_EXC_MATCH"]
    POP_TOP = dis.opmap["POP_TOP"]
    LOAD_GLOBAL = dis.opmap["LOAD_GLOBAL"]
    SETUP_FINALLY = dis.opmap["SETUP_FINALLY"]

    injection_indexes = set()

    lines_offsets = [o for o, _ in dis.findlinestarts(code)]

    def inject_conditionally(offset: int):
        if offset in lines_offsets:
            injection_indexes.add(offset)

    def inject_next_offset_start_of_line(offset: int):
        while offset not in lines_offsets:
            offset += 2
            if offset > lines_offsets[-1]:
                offset = lines_offsets[-1]
                break
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
        if current_opcode == JUMP_IF_NOT_EXC_MATCH:
            potential_marks.add((current_arg << 1))
            continue

        if idx in potential_marks:
            if current_opcode == DUP_TOP:
                if co_code[idx + 2] == LOAD_GLOBAL and co_code[idx + 4] == JUMP_IF_NOT_EXC_MATCH:
                    inject_next_offset_start_of_line(idx + 6)

            elif current_opcode == POP_TOP:
                # an except block is always the first opcode of a line
                # while finally has the same structure but has no line
                if idx in lines_offsets:
                    inject_next_offset_start_of_line(first_offset_not_matching(idx, POP_TOP))
            continue

        if current_opcode == SETUP_FINALLY:
            target = idx + (current_arg << 1) + 2
            potential_marks.add(target)
            continue

    return sorted(list(injection_indexes))


def _find_except_bytecode_indexes_3_11(code: CodeType) -> t.List[int]:
    CACHE = dis.opmap["CACHE"]
    CHECK_EXC_MATCH = dis.opmap["CHECK_EXC_MATCH"]
    POP_JUMP_FORWARD_IF_FALSE = dis.opmap["POP_JUMP_FORWARD_IF_FALSE"]
    POP_TOP = dis.opmap["POP_TOP"]
    PUSH_EXC_INFO = dis.opmap["PUSH_EXC_INFO"]

    def mark_for_injection(index: int):
        if code.co_code[index] != POP_TOP:
            injection_indexes.add(index)
        else:
            mark_for_injection(index + 2)

    def first_opcode_not_matching(start: int, *opcodes: int):
        while code.co_code[start] in opcodes:
            start += 2
        return code.co_code[start]

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

    injection_indexes = set()
    co_code = code.co_code
    for idx in range(0, len(code.co_code), 2):
        # Typed exception handlers
        if co_code[idx] == CHECK_EXC_MATCH and co_code[idx + 2] == POP_JUMP_FORWARD_IF_FALSE:
            injection_indexes.add(idx + 2 + 2 + 2)

        # Generic exception handlers
        elif co_code[idx] == PUSH_EXC_INFO and CHECK_EXC_MATCH != nth_non_cache_opcode(idx, 3):
            injection_indexes.add(first_offset_not_matching(idx + 2, POP_TOP, CACHE))

    return sorted(list(injection_indexes))

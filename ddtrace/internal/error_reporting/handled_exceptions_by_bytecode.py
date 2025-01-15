import dis
import sys
from types import CodeType
import typing as t

from ..bytecode_injection.core import CallbackType
from ..bytecode_injection.core import InjectionContext
from ..bytecode_injection.core import inject_invocation
from .hook import _default_datadog_exc_callback


# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
# NOTE: the "prettier" one-liner version (eg: assert (3,11) <= sys.version_info < (3,12)) does not work for mypy
assert sys.version_info >= (3, 10)  # and sys.version_info < (3, 12)  # nosec


def _inject_handled_exception_reporting(func, callback: CallbackType | None = None):
    func = func.__wrapped__ if hasattr(func, "__wrapped__") else func
    original_code = func.__code__  # type: CodeType

    callback = callback or _default_datadog_exc_callback

    if sys.version_info[:2] == (3, 10):
        injection_indexes = _find_bytecode_indexes_3_10(original_code)
    elif sys.version_info[:2] == (3, 11):
        injection_indexes = _find_bytecode_indexes_3_11(original_code)
    else:
        raise NotImplementedError(f"Unsupported python version: {sys.version_info}")

    if not injection_indexes:
        return

    def injection_lines_cb(_: InjectionContext):
        return [opcode for opcode, _ in dis.findlinestarts(original_code) if opcode in injection_indexes]

    injection_context = InjectionContext(original_code, callback, injection_lines_cb)

    code, _ = inject_invocation(injection_context, "path/to/file.py", "my.package")
    func.__code__ = code


def _find_bytecode_indexes_3_10(code: CodeType) -> t.List[int]:
    DUP_TOP = dis.opmap["DUP_TOP"]
    JUMP_IF_NOT_EXC_MATCH = dis.opmap["JUMP_IF_NOT_EXC_MATCH"]
    POP_TOP = dis.opmap["POP_TOP"]
    SETUP_FINALLY = dis.opmap["SETUP_FINALLY"]

    injection_indexes = set()

    lines_offsets = [o for o, _ in dis.findlinestarts(code)]

    def inject_conditionally(offset: int):
        if offset in lines_offsets:
            injection_indexes.add(offset)

    def first_offset_not_matching(start: int, *opcodes: int):
        while code.co_code[start] in opcodes:
            start += 2
        return start

    potential_marks = set()

    co_code = code.co_code
    waiting_for_except = False
    for idx in range(0, len(code.co_code), 2):
        current_opcode = co_code[idx]
        current_arg = co_code[idx + 1]
        if current_opcode == JUMP_IF_NOT_EXC_MATCH:
            target = current_arg << 1
            potential_marks.add(target)
            continue

        if idx in potential_marks:
            if current_opcode == DUP_TOP:
                waiting_for_except = True
            elif current_opcode == POP_TOP:
                inject_conditionally(first_offset_not_matching(idx, POP_TOP))
            continue

        if current_opcode == SETUP_FINALLY:
            if waiting_for_except:
                waiting_for_except = False
                inject_conditionally(idx + 2)
            else:
                target = idx + (current_arg << 1) + 2
                potential_marks.add(target)
                continue

    return sorted(list(injection_indexes))


def _find_bytecode_indexes_3_11(code: CodeType) -> t.List[int]:
    CACHE = dis.opmap["CACHE"]
    CHECK_EXC_MATCH = dis.opmap["CHECK_EXC_MATCH"]
    POP_JUMP_FORWARD_IF_FALSE = dis.opmap["POP_JUMP_FORWARD_IF_FALSE"]
    POP_TOP = dis.opmap["POP_TOP"]
    PUSH_EXC_INFO = dis.opmap["PUSH_EXC_INFO"]
    STORE_FAST = dis.opmap["STORE_FAST"]

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
    for idx in range(len(code.co_code)):
        # Typed exception handlers
        if co_code[idx] == CHECK_EXC_MATCH and co_code[idx + 2] == POP_JUMP_FORWARD_IF_FALSE:
            if co_code[idx + 2 + 2] == STORE_FAST:
                injection_indexes.add(idx + 2 + 2 + 2)
            # if the code unit AFTER the value pointed by POP_JUMP_FORWARD_IF_FALSE is not a CHECK_EXC_MATCH, then
            # we should inject, as it could be an generic except
            jump_pointed_offset = idx + 2 + (co_code[idx + 2 + 1] << 1) + 2
            if first_opcode_not_matching(jump_pointed_offset + 2, CACHE) != CHECK_EXC_MATCH:
                mark_for_injection(jump_pointed_offset)

        # Generic exception handlers
        if co_code[idx] == PUSH_EXC_INFO and CHECK_EXC_MATCH != nth_non_cache_opcode(idx, 3):
            injection_indexes.add(first_offset_not_matching(idx + 2, POP_TOP, CACHE))

    return sorted(list(injection_indexes))

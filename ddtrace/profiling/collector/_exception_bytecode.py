# Bytecode injection for exception profiling on Python 3.10 and 3.11.
#
# This module instruments except blocks to call our profiling callback,
# enabling exception profiling on Python versions without sys.monitoring.

import dis
import sys
import types
from types import CodeType
from types import ModuleType
import typing as t

from ddtrace.internal.bytecode_injection.core import CallbackType
from ddtrace.internal.bytecode_injection.core import InjectionContext
from ddtrace.internal.bytecode_injection.core import inject_invocation
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import BaseModuleWatchdog


log = get_logger(__name__)

INSTRUMENTABLE_TYPES = (types.FunctionType, types.MethodType, staticmethod, type)

# Version-specific bytecode offset finders
py_version = sys.version_info[:2]


def _find_except_bytecode_indexes_3_10(code: CodeType) -> t.List[int]:
    """Find the offset of the starting line after the except keyword for Python 3.10

    There are two ways of detecting an except in the bytecodes:
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

    injection_indexes: t.Set[int] = set()
    lines_offsets = [o for o, _ in dis.findlinestarts(code)]

    def inject_next_start_of_line_offset(offset: int):
        # Find the first offset of the next line
        while offset not in lines_offsets:
            offset += 2
            if offset > lines_offsets[-1]:
                return
        injection_indexes.add(offset)

    def first_offset_not_matching(start: int, *opcodes: int):
        while code.co_code[start] in opcodes:
            start += 2
        return start

    potential_marks: t.Set[int] = set()
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
                    inject_next_start_of_line_offset(idx + 6)
            elif current_opcode == POP_TOP:
                if idx in lines_offsets:
                    inject_next_start_of_line_offset(first_offset_not_matching(idx, POP_TOP))
            continue

        if current_opcode == SETUP_FINALLY:
            target = idx + (current_arg << 1) + 2
            potential_marks.add(target)
            continue

    return sorted(list(injection_indexes))


def _find_except_bytecode_indexes_3_11(code: CodeType) -> t.List[int]:
    """Find the offset of the starting line after the except keyword for Python 3.11

    There are two ways of detecting an except in the bytecodes:
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
    injection_indexes: t.Set[int] = set()
    co_code = code.co_code

    def inject_next_start_of_line_offset(offset: int):
        # Find the first offset of the next line
        while offset not in lines_offsets:
            offset += 2
            if offset > lines_offsets[-1]:
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

    for idx in range(0, len(code.co_code), 2):
        # Typed exception handlers
        if co_code[idx] == CHECK_EXC_MATCH and co_code[idx + 2] == POP_JUMP_FORWARD_IF_FALSE:
            inject_next_start_of_line_offset(idx + 6)
        # Generic exception handlers
        elif co_code[idx] == PUSH_EXC_INFO and CHECK_EXC_MATCH != nth_non_cache_opcode(idx, 3):
            inject_next_start_of_line_offset(first_offset_not_matching(idx + 2, POP_TOP, CACHE))

    return sorted(list(injection_indexes))


# Select the appropriate offset finder for the current Python version
if py_version == (3, 10):

    def _get_offsets(ctx: InjectionContext) -> t.List[int]:
        return _find_except_bytecode_indexes_3_10(ctx.original_code)

    _offsets_callback = _get_offsets
elif py_version == (3, 11):

    def _get_offsets(ctx: InjectionContext) -> t.List[int]:
        return _find_except_bytecode_indexes_3_11(ctx.original_code)

    _offsets_callback = _get_offsets
else:

    def _get_offsets_noop(ctx: InjectionContext) -> t.List[int]:
        return []

    _offsets_callback = _get_offsets_noop


def _inject_exception_profiling(func, callback: CallbackType) -> None:
    # Inject profiling callback at except clause bytecode offsets.
    # If the function has a wrapper, instrument the wrapped code
    code_to_instr = func.__wrapped__ if hasattr(func, "__wrapped__") else func
    if not hasattr(code_to_instr, "__code__"):
        return

    original_code: CodeType = code_to_instr.__code__

    # Create injection context and inject
    injection_context = InjectionContext(original_code, callback, _offsets_callback)
    code, _ = inject_invocation(injection_context, original_code.co_filename, "ddtrace.profiling")

    try:
        code_to_instr.__code__ = code
    except Exception:
        log.debug("Could not set the code of %s", code_to_instr, exc_info=True)


class ExceptionProfilingInjector:
    # Injects exception profiling callbacks into module functions.

    _instrumented_modules: t.Set[str]
    _instrumented_obj: t.Set[int]
    _callback: CallbackType

    def __init__(self, callback: CallbackType):
        self._instrumented_modules = set()
        self._instrumented_obj = set()
        self._callback = callback

    def instrument_module(self, module_name: str) -> None:
        # Instrument all functions in a module.
        if module_name not in sys.modules:
            return

        module = sys.modules[module_name]

        # Skip modules without files (builtins, etc.)
        if not hasattr(module, "__file__") or module.__file__ is None:
            return

        # Skip already instrumented
        if module_name in self._instrumented_modules:
            return
        self._instrumented_modules.add(module_name)

        # Instrument all functions and classes in the module
        for name in dir(module):
            if name in module.__dict__:
                obj = module.__dict__[name]
                if (
                    type(obj) in INSTRUMENTABLE_TYPES
                    and (module_name == "__main__" or getattr(obj, "__module__", None) == module_name)
                    and not name.startswith("__")
                ):
                    self._instrument_obj(obj)

    def _instrument_obj(self, obj) -> None:
        # Instrument a function, method, or class.
        obj_hash = hash(obj)
        if obj_hash in self._instrumented_obj:
            return
        self._instrumented_obj.add(obj_hash)

        if type(obj) in (types.FunctionType, types.MethodType, staticmethod):
            if hasattr(obj, "__name__") and not self._is_reserved(obj.__name__):
                _inject_exception_profiling(obj, self._callback)
        elif isinstance(obj, type):
            # Instrument class methods
            for attr_name in obj.__dict__.keys():
                attr = obj.__dict__[attr_name]
                if type(attr) in INSTRUMENTABLE_TYPES and hash(attr) not in self._instrumented_obj:
                    self._instrument_obj(attr)

    def _is_reserved(self, name: str) -> bool:
        # Check if function name is reserved (skip instrumentation).
        return name.startswith("__") and name != "__call__"


# Global state
_injector: t.Optional[ExceptionProfilingInjector] = None
_callback: t.Optional[CallbackType] = None


class ExceptionProfilingWatchdog(BaseModuleWatchdog):
    # Watches for module imports and instruments them for exception profiling.

    def __init__(self):
        super().__init__()
        global _injector
        if _callback is None:
            raise RuntimeError("Callback must be set before installing watchdog")
        _injector = ExceptionProfilingInjector(_callback)

        # Instrument already-loaded modules (including __main__)
        existing_modules = set(sys.modules.keys())
        for module_name in existing_modules:
            _injector.instrument_module(module_name)

    def after_import(self, module: ModuleType) -> None:
        # Called after a module is imported.
        if _injector is not None:
            _injector.instrument_module(module.__name__)


def install_bytecode_exception_profiling(callback: CallbackType) -> None:
    # Install bytecode-based exception profiling for Python 3.10/3.11.
    global _callback

    if py_version not in ((3, 10), (3, 11)):
        log.warning("Bytecode exception profiling only supports Python 3.10 and 3.11")
        return

    _callback = callback
    ExceptionProfilingWatchdog.install()
    log.debug("Bytecode exception profiling installed")


def uninstall_bytecode_exception_profiling() -> None:
    # Uninstall the bytecode exception profiling watchdog.
    ExceptionProfilingWatchdog.uninstall()
    log.debug("Bytecode exception profiling uninstalled")

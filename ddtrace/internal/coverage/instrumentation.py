from types import CodeType
import typing as t

from bytecode import Bytecode
from bytecode import Instr

from ddtrace.internal.injection import INJECTION_ASSEMBLY
from ddtrace.internal.injection import HookType


def instrument_all_lines(code: CodeType, hook: HookType, path: str) -> t.Tuple[CodeType, t.Set[int]]:
    abstract_code = Bytecode.from_code(code)
    # Avoid instrumenting the same code object multiple times
    if code.co_name == "<module>" and (code.co_filename.endswith("acoverage/included_path/lib.py") or code.co_filename.endswith(
        "coverage/included_path/import_time_lib.py")
    ):
        print(code)
        import dis
        dis.dis(code)
    #     # breakpoint()
    lines = set()

    is_module = code.co_name == "<module>"
    function_lines = set()
    instrumented_lines = set()
    module_consts_to_lines = {}
    current_import_name = ""

    last_lineno = None
    for i, instr in enumerate(abstract_code):
        if not isinstance(instr, Instr):
            continue

        try:
            if instr.lineno is None:
                continue

            # Store information about all module-level
            if is_module:
                # If we're making a function, store the line number
                if instr.name == "MAKE_FUNCTION":
                    function_lines.add(instr.lineno)

                # If we're storing a name at and the current line is not a function, then we must be storing a module-
                # level constant
                if instr.name == "STORE_NAME" and instr.lineno not in function_lines:
                    module_consts_to_lines[instr.arg] = instr.lineno

            # If the current instruction is an import (regardless of where in the module that import is happening),
            # transform that


            if instr.lineno == last_lineno:
                continue

            last_lineno = instr.lineno

            if instr.name == "RESUME":
                continue

            # Only inject the hook at the beginning of the line if we have not already done so
            if instr.lineno not in instrumented_lines:
                abstract_code[i:i] = INJECTION_ASSEMBLY.bind(dict(hook=hook, arg=(path, last_lineno)), lineno=last_lineno)
                instrumented_lines.add(instr.lineno)

            # Track the line number
            lines.add(last_lineno)
        except AttributeError:
            # pseudo-instruction (e.g. label)
            pass

    return abstract_code.to_code(), lines, module_consts_to_lines
